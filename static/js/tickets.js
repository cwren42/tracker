(function(){
  const cfg    = window.TICKETS_CFG || {};
  const labels = cfg.chart_labels || [];
  const data   = cfg.chart_data   || [];
  const short  = labels.map(d => { const p = d.split('-'); return p[1]+'/'+p[2]; });
  const ctx = document.getElementById('ticketChart');
  if (!ctx) return;
  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: short,
      datasets: [{
        label: 'Tickets',
        data,
        backgroundColor: data.map(v => v > 0 ? 'rgba(13,110,253,0.3)' : 'rgba(0,0,0,0.05)'),
        borderColor: 'rgba(13,110,253,0.7)',
        borderWidth: 1,
        borderRadius: 4,
        hoverBackgroundColor: 'rgba(13,110,253,0.55)',
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: (items) => labels[items[0].dataIndex],
        label: (item) => ` ${item.raw} ticket${item.raw !== 1 ? 's' : ''}`
      }}},
      scales: {
        y: { beginAtZero: true, ticks: { stepSize: 1, precision: 0 } },
        x: { ticks: { maxTicksLimit: 10 } }
      }
    }
  });
})();
