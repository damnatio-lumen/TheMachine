(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { cameraRunning: false, sourceType: 'CAMERA', faces: [], selectedTrack: null, assets: [], snapshots: [], videos: [], latestSnapshot: null, pollTimer: null, toastTimer: null, streamFailed: false, frameWaitTimer: null, biometric: {modelsReady:false, enabled:false, threshold:0.40}, lastBioRefresh:0, lastCandidateRefresh:0 };
  const CLASS = {
    UNCLASSIFIED: {label:'UNCLASSIFIED', color:'#b7c0c4', css:'gray'}, ADMIN:{label:'ADMIN',color:'#f3e849',css:'yellow'},
    THREAT:{label:'THREAT · OPERATOR TAG',color:'#f04444',css:'red'}, VIOLENCE_INDICATOR:{label:'VIOLENCE INDICATOR',color:'#f4f4ed',css:'gray'},
    MACHINE_AWARE:{label:'MACHINE-AWARE',color:'#f3e849',css:'yellow'}, ANALOG_INTERFACE:{label:'ANALOG INTERFACE',color:'#e9cf42',css:'yellow'},
    ASSET:{label:'ASSET',color:'#48a5ff',css:'blue'}, PERSON_OF_INTEREST:{label:'PERSON OF INTEREST · OPERATOR TAG',color:'#f3a847',css:'amber'}
  };
  async function api(url, options={}) {
    const response = await fetch(url, {headers:{'Content-Type':'application/json', ...(options.headers||{})}, ...options});
    let data;
    try { data = await response.json(); } catch { data = {error:`HTTP ${response.status}`}; }
    if (!response.ok || data.ok === false) throw new Error(data.error || data.message || `HTTP ${response.status}`);
    return data;
  }
  function toast(message, type='') {
    const el=$('toast'); el.textContent=message; el.className=`toast show ${type}`;
    clearTimeout(state.toastTimer); state.toastTimer=setTimeout(()=>el.className='toast',3800);
  }
  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function fmtDate(value) {
    if (!value) return '—';
    try { return new Date(value).toLocaleString('en-GB'); } catch { return value; }
  }
  function setView(name) {
    document.querySelectorAll('.nav-tab').forEach(button=>button.classList.toggle('active', button.dataset.view===name));
    document.querySelectorAll('.view-page').forEach(page=>page.classList.toggle('active', page.id===`view-${name}`));
    const titles={live:['LIVE IDENTIFICATION','01'],snapshot:['SNAPSHOT / DOSSIER','02'],registry:['ASSET REGISTRY','03']};
    $('viewTitle').textContent=titles[name][0]; $('modeCode').textContent=titles[name][1];
    if(name==='registry') refreshAssets();
    if(name==='snapshot') refreshSnapshots();
  }
  document.querySelectorAll('.nav-tab').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
  function setCameraUi(running, error='') {
    state.cameraRunning=running;
    $('cameraState').textContent=running?(state.sourceType==='VIDEO'?'VIDEO PLAYBACK':'CAMERA ONLINE'):(state.sourceType==='VIDEO'?'VIDEO READY':'OFFLINE');
    $('sourceTitle').textContent=state.sourceType==='VIDEO'?'LOCAL VIDEO FILE':'LOCAL CAMERA';
    $('cameraLed').classList.toggle('online',running);
    $('startCamera').disabled=running; $('stopCamera').disabled=!running;
    $('captureBtn').disabled=!running;
    $('streamState').textContent=running?(state.sourceType==='VIDEO'?'VIDEO':'LIVE'):(state.sourceType==='VIDEO'?'VIDEO ENDED':'STANDBY');
    $('streamState').previousElementSibling?.classList.toggle('online',running);
    $('systemLed').classList.toggle('online',running); $('systemLed').classList.toggle('offline',!running);
    $('systemState').textContent=running?'ONLINE':'STANDBY';
    $('footerStatus').textContent=running?(state.sourceType==='VIDEO'?'VIDEO ACTIVE':'CAMERA ACTIVE'):'IDLE';
    $('detectorStatus').textContent=running?'ACTIVE':'READY';
    $('startVideo').disabled=running || !$('videoSource').value; $('uploadVideo').disabled=running; $('startCamera').disabled=running; $('cameraIndex').disabled=running; $('videoSource').disabled=running; $('deleteVideo').disabled=running || !$('videoSource').value;
    $('videoFeed').hidden=!running;
    $('noSignal').hidden=false;
    state.streamFailed=false;
    if (state.frameWaitTimer) { clearTimeout(state.frameWaitTimer); state.frameWaitTimer=null; }
    const signalTitle=$('noSignal').querySelector('strong');
    const signalText=$('noSignal').querySelector('p');
    const signalSmall=$('noSignal').querySelector('small');
    if (running) {
      signalTitle.textContent='WAITING FOR FIRST FRAME';
      signalText.textContent=state.sourceType==='VIDEO'?'Video opened. Waiting for an actual frame…':'Camera opened. Waiting for an actual video frame…';
      signalSmall.textContent=state.sourceType==='VIDEO'?'CHECK FILE CODEC / DECODER IF THIS PERSISTS':'IF THIS PERSISTS, CHECK CAMERA INDEX / PRIVACY / OTHER APPS';
      $('videoFeed').src=`/video_feed?ts=${Date.now()}`;
      state.frameWaitTimer=setTimeout(()=>{
        if(state.cameraRunning && $('noSignal').hidden===false && !state.streamFailed){
          signalTitle.textContent='NO VIDEO FRAMES';
          signalText.textContent='The camera opened, but the browser has not received a usable frame.';
          signalSmall.textContent=state.sourceType==='VIDEO'?'TRY ANOTHER ENCODE (MP4/H.264 RECOMMENDED)':'TRY CAMERA 1 / 2 · CLOSE TEAMS, ZOOM, CAMERA APP';
        }
      },6500);
    } else {
      $('videoFeed').removeAttribute('src');
      $('faceCount').textContent='00'; $('feedFaceSummary').textContent='NO TRACKS'; $('feedTimestamp').textContent=error?'CAMERA ERROR':'NO SIGNAL';
      signalTitle.textContent=error?(state.sourceType==='VIDEO'?'VIDEO SOURCE ERROR':'CAMERA ERROR'):'NO VIDEO SIGNAL';
      signalText.textContent=error||(state.sourceType==='VIDEO'?'Select a saved video source and press PLAY SELECTED VIDEO.':'Select a webcam or load a local video file to begin face detection.');
      signalSmall.textContent='VIDEO AND CAMERA PROCESSING STAY ON THIS COMPUTER';
      renderFaces([]);
    }
    if(error) toast(error,'error');
  }
  $('videoFeed').addEventListener('load',()=>{
    if(state.cameraRunning && !state.streamFailed){
      $('noSignal').hidden=true;
      if(state.frameWaitTimer){clearTimeout(state.frameWaitTimer);state.frameWaitTimer=null;}
    }
  });
  $('videoFeed').addEventListener('error',()=>{
    if(!state.cameraRunning)return;
    state.streamFailed=true;
    $('noSignal').hidden=false;
    $('noSignal').querySelector('strong').textContent='VIDEO STREAM ERROR';
    $('noSignal').querySelector('p').textContent='The backend is running, but the browser could not decode the camera feed.';
    $('noSignal').querySelector('small').textContent='REFRESH PAGE · CHECK logs\\machine.log · RESTART FEED';
  });
  $('startCamera').addEventListener('click', async()=>{
    $('startCamera').disabled=true; state.sourceType='CAMERA';
    try { const r=await api('/api/camera/start',{method:'POST',body:JSON.stringify({index:Number($('cameraIndex').value||0)})}); state.sourceType='CAMERA'; setCameraUi(true); toast(r.message||'Camera started'); startPolling(); }
    catch(e){setCameraUi(false,e.message); $('startCamera').disabled=false;}
  });
  async function refreshVideoSources(selectId=''){
    try {
      const data=await api('/api/videos'); state.videos=data.videos||[];
      const select=$('videoSource');
      const previous=selectId||select.value;
      select.innerHTML=state.videos.length?state.videos.map(v=>`<option value="${escapeHtml(v.id)}">${escapeHtml(v.original_name)} · ${(v.size_bytes/1048576).toFixed(1)} MB</option>`).join(''):'<option value="">No video files saved</option>';
      if(previous && state.videos.some(v=>v.id===previous)) select.value=previous;
      $('startVideo').disabled=state.cameraRunning || !select.value;
      $('deleteVideo').disabled=state.cameraRunning || !select.value;
    } catch(e) { toast(`Cannot load video sources: ${e.message}`,'error'); }
  }
  $('videoSource').addEventListener('change',()=>{ $('startVideo').disabled=state.cameraRunning || !$('videoSource').value; $('deleteVideo').disabled=state.cameraRunning || !$('videoSource').value; });
  $('uploadVideo').addEventListener('click',async()=>{
    const input=$('videoFile');
    if(!input.files || !input.files[0]){toast('Choose a video file first.','warning');return;}
    const file=input.files[0];
    if(file.size>512*1024*1024){toast('The video exceeds the 512 MB local upload limit.','error');return;}
    const form=new FormData(); form.append('video',file,file.name);
    $('uploadVideo').disabled=true;
    try{
      const response=await fetch('/api/video/upload',{method:'POST',body:form});
      const result=await response.json();
      if(!response.ok || result.ok===false) throw new Error(result.error||`HTTP ${response.status}`);
      await refreshVideoSources(result.video.id); input.value=''; toast(result.message||'Video source added.');
    }catch(e){toast(e.message,'error');}
    finally{$('uploadVideo').disabled=state.cameraRunning;}
  });
  $('startVideo').addEventListener('click',async()=>{
    const id=$('videoSource').value; state.sourceType='VIDEO';
    if(!id){toast('Add and select a video file first.','warning');return;}
    $('startVideo').disabled=true;
    try{const r=await api('/api/video/start',{method:'POST',body:JSON.stringify({source_id:id})});state.sourceType='VIDEO';setCameraUi(true);toast(r.message||'Video started');startPolling();}
    catch(e){setCameraUi(false,e.message);refreshVideoSources();}
  });
  $('deleteVideo').addEventListener('click',async()=>{
    const id=$('videoSource').value; if(!id)return;
    const record=state.videos.find(v=>v.id===id);
    if(!window.confirm(`Remove local video source ${record?.original_name||id}?`))return;
    try{await api(`/api/videos/${encodeURIComponent(id)}`,{method:'DELETE'});await refreshVideoSources();toast('Video source removed from local archive.');}
    catch(e){toast(e.message,'error');}
  });
  $('stopCamera').addEventListener('click', async()=>{
    try { await api('/api/camera/stop',{method:'POST',body:'{}'}); } catch(e){toast(e.message,'error');}
    stopPolling(); state.selectedTrack=null; state.sourceType='CAMERA'; setCameraUi(false); renderSelection(); toast('Feed stopped. Session-only track links have been cleared.'); refreshStatus(); refreshVideoSources();
  });
  function startPolling(){ if(state.pollTimer) clearInterval(state.pollTimer); refreshStatus(); state.pollTimer=setInterval(refreshStatus,650); }
  function stopPolling(){ if(state.pollTimer) clearInterval(state.pollTimer); state.pollTimer=null; }
  async function refreshStatus(){
    try {
      const data=await api('/api/status');
      if(data.source_type) state.sourceType=data.source_type;
      if(state.cameraRunning!==data.running) setCameraUi(data.running, data.last_error||'');
      if(data.running && data.frame_ready && !state.streamFailed){
        $('noSignal').hidden=true;
        if(state.frameWaitTimer){clearTimeout(state.frameWaitTimer);state.frameWaitTimer=null;}
      } else if(data.running && !data.frame_ready){
        $('noSignal').hidden=false;
        const title=$('noSignal').querySelector('strong');
        const body=$('noSignal').querySelector('p');
        if(data.last_error){title.textContent='CAMERA FRAME ERROR';body.textContent=data.last_error;}
      }
      state.faces=data.faces||[];
      $('faceCount').textContent=String(state.faces.length).padStart(2,'0');
      $('feedFaceSummary').textContent=state.faces.length?`${state.faces.length} FACE TRACK${state.faces.length===1?'':'S'}`:'NO TRACKS';
      if(data.biometric_models_ready && $('detectorLabel')) $('detectorLabel').textContent='DETECTION / YUNET';
      else if($('detectorLabel')) $('detectorLabel').textContent='DETECTION / HAAR CASCADE';
      const now=Date.now(); if(now-state.lastBioRefresh>4000){state.lastBioRefresh=now; refreshBiometricStatus();} if(now-state.lastCandidateRefresh>8000){state.lastCandidateRefresh=now; refreshBiometricCandidates();} 
      $('feedTimestamp').textContent=data.last_frame_at?fmtDate(data.last_frame_at):'NO SIGNAL';
      const sourceHeading=document.querySelector('.feed-topline span'); if(sourceHeading) sourceHeading.textContent=`${data.source_type==='VIDEO'?'VIDEO':'CAM'} / ${data.source_label||'LOCAL FEED'} · FRONTAL FACE DETECTION`;
      renderFaces(state.faces); renderSelection();
    } catch(e) { /* UI remains usable while backend starts */ }
  }
  function renderFaces(faces){
    const wrap=$('detectedFaces');
    if(!faces.length){wrap.innerHTML=`<div class="quiet-empty">${state.cameraRunning?'No face detected in the current frames.':'Waiting for a camera feed…'}</div>`;return;}
    wrap.innerHTML=faces.map(face=>{
      const isSelected=face.track_id===state.selectedTrack;
      const k=face.style==='red'?'red':face.style==='blue'?'blue':(face.style==='admin'||face.style.includes('yellow'))?'yellow':'';
      const label=face.admin_tag?'ADMIN':(face.label||'UNCLASSIFIED');
      return `<button class="face-card ${isSelected?'selected':''}" data-track="${escapeHtml(face.track_id)}" type="button">
        <span class="face-card-top"><span class="mini-face-reticle ${k}" style="border-color:${escapeHtml(face.color||'#b7c0c4')}"></span></span>
        <span class="face-card-info"><b>${escapeHtml(face.track_id)}</b><span>${escapeHtml(face.asset_id||'NO PROFILE LINK')}</span><small class="track-state ${face.detected_now===false?'held':'locked'}">${face.detected_now===false?'HOLD / REACQUIRE':(face.matched_by==='BIOMETRIC'?'BIOMETRIC RELOCK':'TRACK LOCKED')}</small><em class="${k}" style="color:${escapeHtml(face.color||'#b7c0c4')}">${escapeHtml(label)}</em>${face.matched_by==='BIOMETRIC'?`<small class="bio-score">COSINE ${Number(face.match_score||0).toFixed(3)}</small>`:''}</span>
      </button>`;
    }).join('');
    wrap.querySelectorAll('[data-track]').forEach(btn=>btn.addEventListener('click',()=>{state.selectedTrack=btn.dataset.track;renderFaces(state.faces);renderSelection();}));
  }
  function selectedFace(){return state.faces.find(f=>f.track_id===state.selectedTrack)||null;}
  function renderSelection(){
    const face=selectedFace();
    $('emptySelection').hidden=!!face; $('selectionDetails').hidden=!face;
    $('selectedTrackLabel').textContent=face?face.track_id:'NONE';
    $('registerBtn').disabled=!face;
    $('unbindBtn').disabled=!face||!face.asset_id;
    $('registryRegisterBtn').disabled=!face;
    $('enrollBiometricsBtn').disabled=true;
    if(!face){$('selectedClassChip').textContent='UNCLASSIFIED';$('selectedProfileId').textContent='NONE';return;}
    const classification=face.admin_tag?'ADMIN':(face.label||'UNCLASSIFIED');
    const css=face.style==='red'?'red':face.style==='blue'?'blue':(face.style==='admin'||face.style.includes('yellow'))?'yellow':face.style==='amber'?'amber':'gray';
    $('selectedClassChip').className=`status-chip ${css}`; $('selectedClassChip').textContent=classification;
    $('selectedAlias').textContent=face.alias||'Unclassified track'; $('selectedTrackId').textContent=face.track_id;
    const linkedAsset=face.asset_id?state.assets.find(a=>a.id===face.asset_id):null;
    $('selectedProfileId').textContent=face.asset_id?(face.matched_by==='BIOMETRIC'?`${face.asset_id} / BIOMETRIC` : `${face.asset_id} / MANUAL`):'NONE';
    const sampleCount=Number(linkedAsset?.biometric_sample_count||0);
    $('enrollBiometricsBtn').textContent=sampleCount?'RE-ENROLL COOPERATIVE SAMPLES':'ENROLL COOPERATIVE SAMPLES';
    $('enrollBiometricsBtn').disabled=!(face && linkedAsset && linkedAsset.biometric_opt_in && state.biometric.enabled && state.biometric.modelsReady && face.matched_by!=='BIOMETRIC');
    $('enrollBiometricsBtn').title=!state.biometric.modelsReady?'Install the local YuNet and SFace models first':(!state.biometric.enabled?'Enable biometric matching first':(!linkedAsset?'Link a saved profile first':(!linkedAsset.biometric_opt_in?'Enable participant opt-in in Edit Asset':(sampleCount?'Replace the active sample set after a successful cooperative scan':'Capture a cooperative sample set'))));
    renderAssetLinkSearch();
  }
  $('registerBtn').addEventListener('click',()=>openAssetModal({trackId:state.selectedTrack}));
  $('registryRegisterBtn').addEventListener('click',()=>{
    if(!state.selectedTrack){toast('Select a face in Live Feed first.','warning');setView('live');return;}
    openAssetModal({trackId:state.selectedTrack});
  });
  $('unbindBtn').addEventListener('click',async()=>{
    if(!state.selectedTrack)return;
    try{await api('/api/unbind',{method:'POST',body:JSON.stringify({track_id:state.selectedTrack})});toast('Manual tag cleared for this live track.');await refreshStatus();}
    catch(e){toast(e.message,'error');}
  });
  $('enrollBiometricsBtn').addEventListener('click',async()=>{
    const face=selectedFace(); if(!face || !face.asset_id){toast('Link a saved profile to the selected track first.','warning');return;}
    const asset=state.assets.find(a=>a.id===face.asset_id);
    if(!asset?.biometric_opt_in){toast('Edit this asset and enable biometric matching only with participant opt-in.','warning');return;}
    const replacing=Number(asset.biometric_sample_count||0)>0;
    const warning=replacing?'\n\nThis profile already has templates. The old active templates and any pending expansion samples will be replaced only after the new scan succeeds.':'\n\nThis creates the initial template set for this profile.';
    const ok=window.confirm(`COOPERATIVE ENROLLMENT\n\nProfile: ${asset.alias} (${asset.id})\n\nThe local system will capture up to five face samples over several seconds. Ask the participant to look at the camera and gently turn left and right. Continue only if they have explicitly opted in.${warning}`);
    if(!ok)return;
    const btn=$('enrollBiometricsBtn'); btn.disabled=true; btn.textContent='CAPTURING SAMPLES…';
    try{const r=await api('/api/biometrics/enroll',{method:'POST',body:JSON.stringify({asset_id:asset.id,track_id:face.track_id,replace_existing:true})});toast(r.message||'Cooperative enrollment complete.');await refreshAssets();await refreshBiometricStatus();await refreshStatus();}
    catch(e){toast(e.message,'error');}
    finally{btn.textContent='ENROLL COOPERATIVE SAMPLES';renderSelection();}
  });

  $('captureBtn').addEventListener('click',captureSnapshot);
  async function captureSnapshot(){
    if(!state.cameraRunning){toast('Initialize the camera first.','warning');return;}
    try {
      const data=await api('/api/snapshot',{method:'POST',body:JSON.stringify({track_id:state.selectedTrack||'',notes:$('snapshotNotes').value||''})});
      state.latestSnapshot=data.snapshot;
      $('latestSnapshot').src=data.snapshot.image_url+`?ts=${Date.now()}`; $('latestSnapshot').hidden=false;
      $('snapshotMetadata').innerHTML=`<span>SNAP ID</span><b>${escapeHtml(data.snapshot.id)}</b><span>CAPTURE TIME</span><b>${escapeHtml(fmtDate(data.snapshot.created_at))}</b>`;
      $('openSnapshotFolderHint').disabled=false;
      const face=selectedFace(); const asset=face?.asset_id?state.assets.find(a=>a.id===face.asset_id):null;
      $('snapshotDossier').hidden=true; $('snapshotDossierData').hidden=false;
      $('dossierTrack').textContent=state.selectedTrack||'FULL FRAME'; $('dossierAlias').textContent=asset?.alias||'UNASSIGNED';
      $('dossierClass').textContent=asset?(asset.admin_tag?'ADMIN':(CLASS[asset.classification]?.label||asset.classification)):'UNCLASSIFIED';
      $('dossierAdmin').textContent=asset?(asset.admin_tag?'YES':'NO / NOT ASSIGNED'):'UNKNOWN';
      $('dossierTime').textContent=fmtDate(data.snapshot.created_at); $('dossierAsset').textContent=asset?.id||'NOT LINKED';
      setView('snapshot'); await refreshSnapshots(); toast('Snapshot saved in the local archive.');
    }catch(e){toast(e.message,'error');}
  }
  async function refreshAssets(){
    try {const result=await api('/api/assets');state.assets=result.assets||[];renderAssets();renderAssetLinkSearch();await refreshBiometricCandidates();}catch(e){toast(e.message,'error');}
  }
  function renderAssets(){
    const q=($('assetSearch').value||'').trim().toLowerCase();
    const list=state.assets.filter(a=>[a.id,a.alias,a.classification,a.notes,a.admin_tag?'admin administrator role':'',a.updated_at].some(v=>String(v||'').toLowerCase().includes(q)));
    $('assetCount').textContent=q?`${list.length} MATCH / ${state.assets.length}`:`${state.assets.length} ASSET${state.assets.length===1?'':'S'}`;
    if(!list.length){$('assetGrid').innerHTML='<div class="quiet-empty">No matching assets. Register a new asset from a selected live track.</div>';return;}
    $('assetGrid').innerHTML=list.map(a=>{
      const cls=CLASS[a.classification]||CLASS.UNCLASSIFIED; const col=a.admin_tag?'#f3e849':cls.color;
      const photo=a.reference_image?`<img class="asset-photo" src="/media/profiles/${encodeURIComponent(a.reference_image)}?v=${encodeURIComponent(a.updated_at)}" alt="Reference crop for ${escapeHtml(a.alias)}">`:`<div class="asset-photo-placeholder">□</div>`;
      return `<article class="asset-card"><div class="asset-card-header">${photo}<div><div class="asset-id">${escapeHtml(a.id)}</div><div class="asset-alias">${escapeHtml(a.alias)}</div><div class="asset-class"><span class="asset-class-badge ${a.admin_tag?'admin-badge':escapeHtml(cls.css)}" style="--asset-color:${col}">${escapeHtml(a.admin_tag?'ADMIN':cls.label)}</span></div></div></div><div class="asset-card-notes">${escapeHtml(a.notes||'No dossier notes.')}</div><div class="asset-biometric-status">BIOMETRICS: ${a.biometric_opt_in?'<strong>OPTED-IN</strong>':'OFF'} · ${(a.biometric_sample_count||0)} TEMPLATE(S)${a.auto_expand_opt_in?' · REVIEW EXPANSION ON':''}</div><div class="asset-card-actions"><button data-assign="${escapeHtml(a.id)}" title="Manually tag selected visible face">LINK TO TRACK</button><button data-edit="${escapeHtml(a.id)}">EDIT</button><button data-delete="${escapeHtml(a.id)}">DELETE</button></div></article>`;
    }).join('');
    $('assetGrid').querySelectorAll('[data-assign]').forEach(b=>b.addEventListener('click',()=>assignAsset(b.dataset.assign)));
    $('assetGrid').querySelectorAll('[data-edit]').forEach(b=>b.addEventListener('click',()=>{const a=state.assets.find(v=>v.id===b.dataset.edit);if(a)openAssetModal({edit:a});}));
    $('assetGrid').querySelectorAll('[data-delete]').forEach(b=>b.addEventListener('click',()=>deleteAsset(b.dataset.delete)));
  }
  $('assetSearch').addEventListener('input',renderAssets);
  $('trackAssetSearch').addEventListener('input',renderAssetLinkSearch);
  function renderAssetLinkSearch(){
    const wrap=$('trackAssetResults');
    if(!wrap)return;
    if(!state.selectedTrack){wrap.innerHTML='<div class="quiet-empty">Select a live track before linking a profile.</div>';return;}
    const q=($('trackAssetSearch').value||'').trim().toLowerCase();
    const matches=state.assets.filter(a=>[a.id,a.alias,a.classification,a.notes,a.admin_tag?'admin administrator':''].some(v=>String(v||'').toLowerCase().includes(q)));
    const list=matches.slice(0,5);
    if(!list.length){wrap.innerHTML='<div class="quiet-empty">No matching registered profile.</div>';return;}
    wrap.innerHTML=list.map(a=>{
      const cls=CLASS[a.classification]||CLASS.UNCLASSIFIED;
      const label=a.admin_tag?'ADMIN':cls.label;
      return `<div class="track-asset-result"><span class="track-asset-status ${a.admin_tag?'admin':''}" style="--asset-color:${a.admin_tag?'#f3e849':cls.color}"></span><span class="track-asset-copy"><b>${escapeHtml(a.alias)}</b><small>${escapeHtml(a.id)} · ${escapeHtml(label)}</small></span><button type="button" class="btn btn-quiet" data-link-existing="${escapeHtml(a.id)}">LINK</button></div>`;
    }).join('');
    wrap.querySelectorAll('[data-link-existing]').forEach(btn=>btn.addEventListener('click',()=>assignAsset(btn.dataset.linkExisting)));
    if(matches.length>list.length){wrap.insertAdjacentHTML('beforeend',`<div class="track-asset-more">${matches.length-list.length} more matches · refine search</div>`);}
  }
  async function assignAsset(assetId){
    if(!state.selectedTrack){toast('Select a currently visible face in Live Feed, then link the profile manually.','warning');setView('live');return;}
    try {const data=await api('/api/bind',{method:'POST',body:JSON.stringify({track_id:state.selectedTrack,asset_id:assetId})});toast(data.message||'Profile linked to current track.');setView('live');await refreshStatus();}
    catch(e){toast(e.message,'error');}
  }
  async function deleteAsset(assetId){
    const asset=state.assets.find(a=>a.id===assetId);if(!asset)return;
    if(!confirm(`Delete ${asset.alias} (${asset.id}) and its stored reference crop? Snapshot records will remain.`))return;
    try{await api(`/api/assets/${encodeURIComponent(assetId)}`,{method:'DELETE'});toast('Asset deleted.');await refreshAssets();await refreshStatus();}
    catch(e){toast(e.message,'error');}
  }
  function openAssetModal({trackId=null,edit=null}={}){
    $('assetForm').reset(); $('editAssetId').value=edit?.id||''; $('formTrackId').value=trackId||'';
    $('modalTitle').textContent=edit?'EDIT ASSET PROFILE':'REGISTER NEW ASSET';
    $('assetAlias').value=edit?.alias||''; $('assetClassification').value=edit?.classification||'UNCLASSIFIED'; $('assetAdmin').value=String(!!edit?.admin_tag); $('assetNotes').value=edit?.notes||'';
    $('biometricOptIn').checked=!!edit?.biometric_opt_in; $('autoExpandOptIn').checked=!!edit?.auto_expand_opt_in; $('autoExpandOptIn').disabled=!$('biometricOptIn').checked;
    $('biometricConsentAck').checked=false; $('biometricConsentRow').hidden=!($('biometricOptIn').checked && (!edit || !edit.biometric_opt_in));
    $('consentRow').hidden=!!edit; $('consentAck').required=!edit; $('consentAck').checked=false;
    $('registrationContext').textContent=edit?`${edit.id} · Reference image remains unchanged. Changes apply only to the profile settings and authorization flags.`:`Selected live track: ${trackId||'none'}. The app saves a reference crop and links the profile to this current track only.`;
    $('assetModal').hidden=false; $('assetAlias').focus();
  }
  function closeAssetModal(){$('assetModal').hidden=true;}
  $('closeModal').addEventListener('click',closeAssetModal);$('cancelModal').addEventListener('click',closeAssetModal);
  $('assetModal').addEventListener('click',e=>{if(e.target===$('assetModal'))closeAssetModal();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAssetModal();});
  $('assetAdmin').addEventListener('change',()=>{
    if($('assetAdmin').value==='true') $('assetClassification').value='ADMIN';
    else if($('assetClassification').value==='ADMIN') $('assetClassification').value='UNCLASSIFIED';
  });
  $('assetClassification').addEventListener('change',()=>{
    if($('assetClassification').value==='ADMIN') $('assetAdmin').value='true';
    else $('assetAdmin').value='false';
  });
  $('biometricOptIn').addEventListener('change',()=>{
    $('autoExpandOptIn').disabled=!$('biometricOptIn').checked;
    if(!$('biometricOptIn').checked) $('autoExpandOptIn').checked=false;
    const edit=state.assets.find(a=>a.id===$('editAssetId').value);
    const needsNewAck=$('biometricOptIn').checked && (!edit || !edit.biometric_opt_in);
    $('biometricConsentRow').hidden=!needsNewAck;
    $('biometricConsentAck').checked=false;
  });
  $('assetForm').addEventListener('submit',async e=>{
    e.preventDefault();
    const editId=$('editAssetId').value; const payload={alias:$('assetAlias').value,classification:$('assetClassification').value,admin_tag:$('assetAdmin').value==='true',notes:$('assetNotes').value,biometric_opt_in:$('biometricOptIn').checked,auto_expand_opt_in:$('autoExpandOptIn').checked,biometric_consent_ack:$('biometricConsentAck').checked};
    const existing=state.assets.find(a=>a.id===editId);
    if(payload.biometric_opt_in && (!existing || !existing.biometric_opt_in) && !payload.biometric_consent_ack){toast('Explicit participant biometric consent must be confirmed before enabling matching.','warning');return;}
    if(editId){
      try{await api(`/api/assets/${encodeURIComponent(editId)}`,{method:'PUT',body:JSON.stringify(payload)});closeAssetModal();toast('Asset profile updated.');await refreshAssets();await refreshStatus();await refreshBiometricStatus();}
      catch(err){toast(err.message,'error');}return;
    }
    payload.track_id=$('formTrackId').value; payload.consent_ack=$('consentAck').checked;
    const save=$('saveAssetBtn');save.disabled=true;
    try{const r=await api('/api/assets',{method:'POST',body:JSON.stringify(payload)});closeAssetModal();state.selectedTrack=payload.track_id;toast(`${r.asset.id} registered and manually linked.`);await refreshAssets();await refreshStatus();}
    catch(err){toast(err.message,'error');}finally{save.disabled=false;}
  });
  async function refreshBiometricStatus(){
    try{
      const data=await api('/api/biometrics/status');
      state.biometric={modelsReady:!!data.models_ready,enabled:!!data.enabled,threshold:Number(data.threshold||0.40)};
      $('biometricModelStatus').textContent=data.models_ready?'YUNET + SFACE READY':'MODEL NOT INSTALLED';
      $('biometricModelStatus').className=data.models_ready?'':'muted';
      $('biometricEnabled').checked=!!data.enabled; $('biometricEnabled').disabled=!data.models_ready;
      $('biometricThreshold').value=String(state.biometric.threshold); $('thresholdValue').textContent=state.biometric.threshold.toFixed(2); $('biometricThreshold').disabled=!data.models_ready;
      $('biometricEnrolledCount').textContent=String(data.enrolled_assets||0).padStart(2,'0');
      $('biometricPendingCount').textContent=String(data.pending_candidates||0).padStart(2,'0');
      if(!data.models_ready) $('biometricModelStatus').title=data.model_error||'Run scripts/download-biometric-models.ps1';
      renderSelection();
    }catch(e){/* The core camera GUI remains available if the optional biometric endpoint is unavailable. */}
  }
  async function saveBiometricSettings(){
    try{
      const data=await api('/api/biometrics/settings',{method:'POST',body:JSON.stringify({enabled:$('biometricEnabled').checked,threshold:Number($('biometricThreshold').value)})});
      state.biometric.enabled=!!data.enabled; state.biometric.threshold=Number(data.threshold); toast(data.message||'Biometric settings saved locally.'); await refreshBiometricStatus(); await refreshStatus();
    }catch(e){toast(e.message,'error');await refreshBiometricStatus();}
  }
  $('biometricEnabled').addEventListener('change',saveBiometricSettings);
  $('biometricThreshold').addEventListener('input',()=>{$('thresholdValue').textContent=Number($('biometricThreshold').value).toFixed(2);});
  $('biometricThreshold').addEventListener('change',saveBiometricSettings);

  async function refreshBiometricCandidates(){
    try{
      const data=await api('/api/biometrics/candidates'); const candidates=data.candidates||[]; const wrap=$('biometricCandidates');
      $('candidateQueueCount').textContent=`${candidates.length} PENDING`;
      $('biometricPendingCount').textContent=String(candidates.length).padStart(2,'0');
      if(!candidates.length){wrap.innerHTML='<div class="quiet-empty">No pending samples. New candidates appear only for profiles with supervised expansion enabled.</div>';return;}
      wrap.innerHTML=candidates.map(c=>`<div class="candidate-row"><div class="candidate-meta"><b>${escapeHtml(c.alias)} · ${escapeHtml(c.asset_id)}</b><span>COSINE ${Number(c.similarity).toFixed(3)} · TRACK ${escapeHtml(c.track_id||'—')}</span><small>${escapeHtml(fmtDate(c.created_at))} · PENDING REVIEW · EMBEDDING ONLY</small></div><div class="candidate-actions"><button class="btn btn-yellow" data-candidate-approve="${escapeHtml(c.id)}">APPROVE SAMPLE</button><button class="btn btn-quiet" data-candidate-reject="${escapeHtml(c.id)}">REJECT</button></div></div>`).join('');
      wrap.querySelectorAll('[data-candidate-approve]').forEach(b=>b.addEventListener('click',()=>reviewCandidate(b.dataset.candidateApprove,'approve')));
      wrap.querySelectorAll('[data-candidate-reject]').forEach(b=>b.addEventListener('click',()=>reviewCandidate(b.dataset.candidateReject,'reject')));
    }catch(e){/* keep registry usable while biometric models are missing */}
  }
  async function reviewCandidate(id,action){
    const wording=action==='approve'?'Add this candidate embedding to the active template set?':'Reject and discard this candidate from active matching?';
    if(!window.confirm(wording))return;
    try{const data=await api(`/api/biometrics/candidates/${encodeURIComponent(id)}/${action}`,{method:'POST',body:'{}'});toast(data.message||`Candidate ${action}d.`);await refreshBiometricCandidates();await refreshAssets();await refreshBiometricStatus();}
    catch(e){toast(e.message,'error');}
  }

  async function refreshSnapshots(){
    try{const data=await api('/api/snapshots');state.snapshots=data.snapshots||[];const wrap=$('snapshotHistory');
      if(!state.snapshots.length){wrap.innerHTML='<div class="quiet-empty">No snapshots saved yet.</div>';return;}
      wrap.innerHTML=state.snapshots.map(s=>`<button type="button" class="snapshot-thumb" data-snapshot="${escapeHtml(s.id)}"><img src="${escapeHtml(s.image_url)}?v=${encodeURIComponent(s.created_at)}" alt="Snapshot ${escapeHtml(s.id)}"><b>${escapeHtml(s.id)}</b><span>${escapeHtml(fmtDate(s.created_at))}</span><span>${escapeHtml(s.track_id||'FULL FRAME')}</span></button>`).join('');
      wrap.querySelectorAll('[data-snapshot]').forEach(b=>b.addEventListener('click',()=>loadSnapshot(b.dataset.snapshot)));
    }catch(e){/* keep snapshot view available offline */}
  }
  async function loadSnapshot(id){
    const item=state.snapshots.find(s=>s.id===id);if(!item)return;
    state.latestSnapshot={id:item.id,created_at:item.created_at,image_url:item.image_url,crop_url:item.crop_url,track_id:item.track_id,asset_id:item.asset_id};
    $('latestSnapshot').src=item.image_url+`?v=${Date.now()}`;$('latestSnapshot').hidden=false;
    $('snapshotMetadata').innerHTML=`<span>SNAP ID</span><b>${escapeHtml(item.id)}</b><span>CAPTURE TIME</span><b>${escapeHtml(fmtDate(item.created_at))}</b>`;
    const asset=state.assets.find(a=>a.id===item.asset_id);
    $('snapshotDossier').hidden=true;$('snapshotDossierData').hidden=false;$('dossierTrack').textContent=item.track_id||'FULL FRAME';$('dossierAlias').textContent=asset?.alias||'UNASSIGNED';$('dossierClass').textContent=asset?(asset.admin_tag?'ADMIN':(CLASS[asset.classification]?.label||asset.classification)):'UNCLASSIFIED';$('dossierAdmin').textContent=asset?(asset.admin_tag?'YES':'NO / NOT ASSIGNED'):'UNKNOWN';$('dossierTime').textContent=fmtDate(item.created_at);$('dossierAsset').textContent=asset?.id||'NOT LINKED';
  }
  $('openSnapshotFolderHint').addEventListener('click',()=>toast('Snapshots are stored in data/snapshots inside the application folder.'));
  $('protocolBtn').addEventListener('click',()=>window.open('/protocol','_blank','noopener,noreferrer'));
  function updateClock(){const d=new Date();$('clock').textContent=d.toLocaleTimeString('en-GB', {hour12:false});}
  setInterval(updateClock,1000);updateClock();
  refreshAssets();refreshSnapshots();refreshStatus();refreshVideoSources();refreshBiometricStatus();refreshBiometricCandidates();
  window.addEventListener('beforeunload',()=>{if(state.pollTimer)clearInterval(state.pollTimer);});
})();
