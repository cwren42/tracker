(function(){
  document.querySelectorAll('.ai-search-bar').forEach(function(bar){
    var input  = bar.querySelector('.ai-search-input');
    var btn    = bar.querySelector('.ai-search-btn');
    var clrBtn = bar.querySelector('.ai-clear-btn');
    var result = bar.querySelector('.ai-search-result');

    function ask(){
      var q = input.value.trim();
      if(!q) return;
      result.classList.remove('d-none');
      result.innerHTML = '<span class="ai-result-thinking"><i class="bi bi-hourglass-split me-1"></i>Thinking…</span>';
      clrBtn.classList.remove('d-none');
      btn.disabled = true;

      fetch('/api/ai/ask', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({question: q})
      })
      .then(function(r){ return r.json(); })
      .then(function(d){
        btn.disabled = false;
        if(d.error){
          result.innerHTML = '<i class="bi bi-exclamation-triangle text-danger me-1"></i>' + d.error;
        } else {
          var answer = d.answer || d.result || JSON.stringify(d, null, 2);
          result.innerHTML = '<i class="bi bi-stars me-2" style="color:#818cf8;"></i><span style="white-space:pre-wrap;">' + answer + '</span>';
        }
      })
      .catch(function(e){
        btn.disabled = false;
        result.innerHTML = '<i class="bi bi-exclamation-triangle text-danger me-1"></i>Network error.';
      });
    }

    btn.addEventListener('click', ask);
    input.addEventListener('keydown', function(e){ if(e.key==='Enter') ask(); });
    clrBtn.addEventListener('click', function(){
      input.value='';
      result.classList.add('d-none');
      clrBtn.classList.add('d-none');
    });
  });
})();
