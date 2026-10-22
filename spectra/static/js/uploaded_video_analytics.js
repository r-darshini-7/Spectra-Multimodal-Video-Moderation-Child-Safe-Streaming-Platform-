document.addEventListener('DOMContentLoaded', () => {
  const canvas = document.getElementById('videoMetricsChart');
  if (!canvas || typeof Chart === 'undefined') return;

  let values;
  try {
    values = JSON.parse(canvas.dataset.values || '[]');
  } catch {
    return;
  }
  if (!Array.isArray(values) || values.length !== 4) return;

  const textColor = getComputedStyle(document.documentElement)
    .getPropertyValue('--text-muted').trim() || '#656b7d';

  new Chart(canvas, {
    type: 'bar',
    data: {
      labels: ['Views', 'Likes', 'Dislikes', 'Comments'],
      datasets: [{
        data: values,
        backgroundColor: ['#6952c8', '#22c55e', '#ef4444', '#0ea5e9'],
        borderRadius: 5,
        maxBarThickness: 42,
      }],
    },
    options: {
      indexAxis: 'y',
      maintainAspectRatio: false,
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => ` ${Number(context.raw).toLocaleString()}`,
          },
        },
      },
      scales: {
        x: {
          beginAtZero: true,
          grace: '15%',
          ticks: { precision: 0, color: textColor },
          grid: { color: 'rgba(100, 110, 130, 0.12)' },
        },
        y: {
          ticks: { color: textColor },
          grid: { display: false },
        },
      },
    },
    plugins: [{
      id: 'barValueLabels',
      afterDatasetsDraw(chart) {
        const chartContext = chart.ctx;
        const bars = chart.getDatasetMeta(0).data;
        chartContext.save();
        chartContext.fillStyle = textColor;
        chartContext.font = '600 12px "Segoe UI", sans-serif';
        chartContext.textBaseline = 'middle';
        values.forEach((value, index) => {
          const bar = bars[index];
          chartContext.fillText(Number(value).toLocaleString(), bar.x + 8, bar.y);
        });
        chartContext.restore();
      },
    }],
  });
});