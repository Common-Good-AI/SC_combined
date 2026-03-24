const SummaryTab = {
  props: {
    preloaded: { type: Object, default: () => ({}) },
  },
  template: `
    <div>
      <div v-if="loading" class="loading">Loading summary data…</div>
      <div v-else-if="error" class="error">{{ error }}</div>
      <template v-else>

        <div class="themes-chart-container">
          <h3>Ideas by Theme</h3>
          <p v-if="!chartData.length"
             style="color:#94a3b8; font-size:0.9rem;">No theme data available.</p>
          <template v-else>
            <p style="color:#64748b; font-size:0.8rem; margin:0 0 0.75rem">
              {{ tags.total_tagged_ideas }} tagged ideas across {{ tags.total_tags }} topics
              · Hover for upvote / downvote breakdown
            </p>
            <div class="themes-chart-wrap"
                 :style="{ height: Math.max(200, chartData.length * 36) + 'px' }">
              <canvas ref="themeChart"></canvas>
            </div>
          </template>
        </div>

      </template>
    </div>
  `,

  data() {
    return {
      loading: true,
      error: null,
      tags: {},
      votesByTag: {},
      chartData: [],
      _themeChart: null,
    };
  },

  async mounted() {
    try {
      this.tags = this.preloaded.tags || {};
      this.votesByTag = this.preloaded.votesByTag || {};
      this._mergeData();
      this.loading = false;

      this.$nextTick(() => {
        this.renderChart();
      });
    } catch (e) {
      console.error('[SummaryTab] mounted error:', e);
      this.error = 'Failed to load summary data.';
      this.loading = false;
    }
  },

  beforeUnmount() {
    if (this._themeChart) this._themeChart.destroy();
  },

  methods: {
    _mergeData() {
      const tagList = this.tags.tags || [];
      const voteMap = {};
      for (const v of (this.votesByTag.tags || [])) {
        voteMap[v.topic_id] = v;
      }
      this.chartData = tagList.map(t => ({
        tag: t.tag,
        topic_id: t.topic_id,
        ideas: t.count,
        upvotes: (voteMap[t.topic_id] || {}).upvotes || 0,
        downvotes: (voteMap[t.topic_id] || {}).downvotes || 0,
        net: (voteMap[t.topic_id] || {}).net || 0,
      }));
    },

    renderChart() {
      const canvas = this.$refs.themeChart;
      if (!canvas || !this.chartData.length) return;
      if (this._themeChart) { this._themeChart.destroy(); this._themeChart = null; }

      const colors = [
        '#003366', '#0564B8', '#059669', '#d97706', '#dc2626',
        '#36A0E0', '#C2DFED', '#E4E0D4', '#84cc16', '#f97316',
      ];
      const labels = this.chartData.map(d => d.tag);
      const data = this.chartData.map(d => d.ideas);
      const rows = this.chartData;

      this._themeChart = new Chart(canvas, {
        type: 'bar',
        data: {
          labels,
          datasets: [{
            label: 'ideas',
            data,
            backgroundColor: data.map((_, i) => colors[i % colors.length]),
            borderRadius: 4,
          }],
        },
        options: {
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { beginAtZero: true, ticks: { precision: 0 }, grid: { display: false } },
            y: {
              ticks: {
                font: { size: 12 },
                callback: function(value) {
                  const label = this.getLabelForValue(value);
                  return label.length > 40 ? label.slice(0, 37) + '…' : label;
                },
              },
            },
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                title: (items) => items[0].label,
                label: (ctx) => {
                  const r = rows[ctx.dataIndex];
                  return [
                    `Ideas: ${r.ideas}`,
                    `Upvotes: ${r.upvotes}`,
                    `Downvotes: ${r.downvotes}`,
                    `Net: ${r.net > 0 ? '+' : ''}${r.net}`,
                  ];
                },
              },
            },
          },
        },
      });
    },
  },
};
