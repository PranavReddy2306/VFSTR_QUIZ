(function(){
  const form = document.getElementById('quizForm');
  if(!form) return;

  function disqualifyAndSubmit(reason){
    try {
      document.getElementById('flag').value = 'disqualified';
    } catch(e){}
    // Optionally clear selections
    const inputs = form.querySelectorAll('input[type=radio]');
    inputs.forEach(i => i.checked = false);
    // Submit
    form.submit();
  }

  // If user changes tab or window hidden
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) disqualifyAndSubmit('tab-switch');
  });

  // If exits fullscreen
  document.addEventListener('fullscreenchange', () => {
    if (!document.fullscreenElement) disqualifyAndSubmit('exit-fullscreen');
  });

  // Try to block common shortcuts (not bulletproof)
  window.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && ['p','s','t','n','w'].includes(e.key.toLowerCase())) {
      e.preventDefault();
    }
    if (e.key === 'F11') e.preventDefault();
  });

  // Before unload (closing or refreshing) – treat as disqualify
  window.addEventListener('beforeunload', (e) => {
    disqualifyAndSubmit('unload');
  });
})();