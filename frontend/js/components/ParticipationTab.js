const ParticipationTab = {
  template: `
    <div>
      <div v-if="loading" class="loading">Loading participation data…</div>
      <div v-else-if="error" class="error">{{ error }}</div>
      <template v-else>

        <!-- Summary cards -->
        <div class="card-row">
          <div class="card">
            <div class="label">Total Participants</div>
            <div class="value">{{ fmt(participants.total) }}</div>
            <div class="sub">
              {{ fmt(participants.confirmed_users) }} confirmed ·
              {{ fmt(participants.email_only_users) }} email-only ·
              {{ fmt(participants.anonymous_users) }} anonymous
            </div>
          </div>
          <div class="card">
            <div class="label">Total Actions</div>
            <div class="value">{{ fmt(actions.total) }}</div>
          </div>
          <div class="card">
            <div class="label">Survey Submissions</div>
            <div class="value">{{ fmt(actions.survey_submits) }}</div>
          </div>
          <div class="card">
            <div class="label">Ideas Submitted</div>
            <div class="value">{{ fmt(actions.ideas_submitted) }}</div>
          </div>
          <div class="card">
            <div class="label">Reactions</div>
            <div class="value">{{ fmt(actions.reactions) }}</div>
          </div>
        </div>

        <!-- Participation over time chart -->
        <div class="chart-container">
          <h3>Participation Over Time</h3>
          <div class="chart-wrap">
            <canvas ref="timelineChart"></canvas>
          </div>
        </div>

        <!-- Breakdown by participant tier -->
        <div class="chart-container">
          <h3>Participants by Category</h3>
          <div class="chart-wrap" style="height:260px">
            <canvas ref="categoryChart"></canvas>
          </div>
        </div>

      </template>
    </div>
  `,

  data() {
    return {
      loading: true,
      error: null,
      participants: {},
      actions: {},
      timeline: [],
      _timelineChart: null,
      _categoryChart: null,
    };
  },

  async mounted() {
    try {
      const [pRes, aRes, tRes] = await Promise.all([
        fetch('/api/analytics/participants'),
        fetch('/api/analytics/actions'),
        fetch('/api/analytics/participation-timeline'),
      ]);
      this.participants = await pRes.json();
      this.actions = await aRes.json();
      const tData = await tRes.json();
      this.timeline = tData.timeline || [];
      this.loading = false;

      this.$nextTick(() => {
        this.renderTimelineChart();
        this.renderCategoryChart();
      });
    } catch (e) {
      this.error = 'Failed to load participation data.';
      this.loading = false;
    }
  },

  beforeUnmount() {
    if (this._timelineChart) this._timelineChart.destroy();
    if (this._categoryChart) this._categoryChart.destroy();
  },

  methods: {
    fmt(n) {
      if (n == null) return '–';
      return Number(n).toLocaleString();
    },

    renderTimelineChart() {
      const ctx = this.$refs.timelineChart;
      if (!ctx || !this.timeline.length) return;

      const labels = this.timeline.map(d => d.date);

      this._timelineChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels,
          datasets: [
            {
              label: 'Total',
              data: this.timeline.map(d => d.total),
              borderColor: '#1e3a5f',
              backgroundColor: 'rgba(30,58,95,.1)',
              fill: true,
              tension: 0.3,
              pointRadius: 3,
            },
            {
              label: 'Surveys',
              data: this.timeline.map(d => d.surveys),
              borderColor: '#3b82f6',
              backgroundColor: 'rgba(59,130,246,.1)',
              tension: 0.3,
              pointRadius: 2,
            },
            {
              label: 'Ideas',
              data: this.timeline.map(d => d.ideas),
              borderColor: '#22c55e',
              backgroundColor: 'rgba(34,197,94,.1)',
              tension: 0.3,
              pointRadius: 2,
            },
            {
              label: 'Reactions',
              data: this.timeline.map(d => d.reactions),
              borderColor: '#f59e0b',
              backgroundColor: 'rgba(245,158,11,.1)',
              tension: 0.3,
              pointRadius: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, ticks: { precision: 0 } },
          },
          plugins: {
            legend: { position: 'top' },
            tooltip: { mode: 'index', intersect: false },
          },
        },
      });
    },

    renderCategoryChart() {
      const ctx = this.$refs.categoryChart;
      if (!ctx) return;

      this._categoryChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
          labels: ['Confirmed Users', 'Email-only Users', 'Anonymous Users'],
          datasets: [{
            data: [
              this.participants.confirmed_users || 0,
              this.participants.email_only_users || 0,
              this.participants.anonymous_users || 0,
            ],
            backgroundColor: ['#1e3a5f', '#3b82f6', '#94a3b8'],
            borderWidth: 2,
            borderColor: '#fff',
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'right' },
          },
        },
      });
    },
  },
};
