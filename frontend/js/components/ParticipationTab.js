const ParticipationTab = {
  props: {
    preloaded: { type: Object, default: () => ({}) },
  },
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

        <!-- GoVocal Participation Rate cards -->
        <div class="card-row" v-if="participationRate && participationRate.rates">
          <div class="card">
            <div class="label">GoVocal Participation Rate (24h)</div>
            <div class="value">{{ participationRate.rates['24h'] ? participationRate.rates['24h'].rate_pct + '%' : '–' }}</div>
            <div class="sub">
              {{ fmt(participationRate.rates['24h'] && participationRate.rates['24h'].users) }} users /
              {{ fmt(participationRate.rates['24h'] && participationRate.rates['24h'].visits) }} visits
            </div>
          </div>
          <div class="card">
            <div class="label">GoVocal Participation Rate (72h)</div>
            <div class="value">{{ participationRate.rates['72h'] ? participationRate.rates['72h'].rate_pct + '%' : '–' }}</div>
            <div class="sub">
              {{ fmt(participationRate.rates['72h'] && participationRate.rates['72h'].users) }} users /
              {{ fmt(participationRate.rates['72h'] && participationRate.rates['72h'].visits) }} visits
            </div>
          </div>
          <div class="card">
            <div class="label">GoVocal Participation Rate (7d)</div>
            <div class="value">{{ participationRate.rates['7d'] ? participationRate.rates['7d'].rate_pct + '%' : '–' }}</div>
            <div class="sub">
              {{ fmt(participationRate.rates['7d'] && participationRate.rates['7d'].users) }} users /
              {{ fmt(participationRate.rates['7d'] && participationRate.rates['7d'].visits) }} visits
            </div>
          </div>
          <div class="card">
            <div class="label">GoVocal Participation Rate (All Time)</div>
            <div class="value">{{ participationRate.all_time ? participationRate.all_time.rate_pct + '%' : '–' }}</div>
            <div class="sub">
              {{ fmt(participationRate.all_time && participationRate.all_time.users) }} users /
              {{ fmt(participationRate.all_time && participationRate.all_time.visits) }} visits
            </div>
          </div>
        </div>

        <!-- Participation over time chart -->
        <div class="chart-container">
          <!-- Chart header: title + controls -->
          <div class="chart-header">
            <h3>Participation Over Time</h3>
            <div class="chart-controls">
              <!-- View mode tabs -->
              <div class="chart-subtabs">
                <button :class="['chart-subtab', { active: chartMode === 'cumulative' }]"
                        @click="setMode('cumulative')">Cumulative</button>
                <button :class="['chart-subtab', { active: chartMode === 'daily' }]"
                        @click="setMode('daily')">Per Day</button>
              </div>
              <!-- Date range -->
              <div class="date-range">
                <label>From <input type="date" v-model="dateFrom" @change="rebuildChart" :max="dateTo || undefined" /></label>
                <label>To <input type="date" v-model="dateTo" @change="rebuildChart" :min="dateFrom || undefined" /></label>
                <button class="reset-dates" @click="resetDates">All time</button>
              </div>
            </div>
          </div>
          <div class="chart-wrap">
            <canvas :key="chartMode" ref="timelineChart"></canvas>
          </div>
        </div>

        <!-- Unique participants over time -->
        <div class="chart-container">
          <div class="chart-header">
            <h3>Unique Participants Over Time</h3>
            <div class="chart-controls">
              <div class="chart-subtabs">
                <button :class="['chart-subtab', { active: sourceMode === 'cumulative' }]"
                        @click="setSourceMode('cumulative')">Cumulative</button>
                <button :class="['chart-subtab', { active: sourceMode === 'daily' }]"
                        @click="setSourceMode('daily')">New Per Day</button>
              </div>
            </div>
          </div>
          <div class="chart-wrap">
            <canvas :key="'src-' + sourceMode" ref="sourceChart"></canvas>
          </div>
        </div>

        <!-- Breakdown by participant tier -->
        <div class="chart-container">
          <h3>Participants by Category</h3>
          <div class="chart-wrap" style="height:260px">
            <canvas ref="categoryChart"></canvas>
          </div>
        </div>

        <!-- Demographic Baseline Breakdown -->
        <div class="chart-container" v-if="demographics && demographics.dimensions">
          <h3>Platform Demographic Baseline <span style="font-size:.75em;color:#64748b;font-weight:normal">(all voters)</span></h3>
          <p style="color:#64748b;font-size:.85em;margin:-.25em 0 1em">
            Distribution of demographics across {{ fmt(demographics.total_voters) }} voters who reacted to ideas.
            This baseline is used to compute consensus (bridging) scores.
          </p>
          <div class="demo-grid">
            <template v-for="dim in demoOrder" :key="dim">
              <div class="demo-card" v-if="demographics.dimensions[dim]">
                <h4>{{ dimLabel(dim) }}</h4>
                <div class="demo-known-info">
                  {{ fmt(demographics.dimensions[dim].total_known) }} known ·
                  {{ fmt(demographics.dimensions[dim].total_unknown) }} unknown
                </div>
                <div class="demo-bar-list">
                  <div class="demo-bar-row" v-for="(pct, cat) in demographics.dimensions[dim].distribution" :key="cat">
                    <div class="demo-bar-label">{{ cat }}</div>
                    <div class="demo-bar-track">
                      <div class="demo-bar-fill" :style="{ width: (pct * 100) + '%', backgroundColor: dimColor(dim) }"></div>
                    </div>
                    <div class="demo-bar-value">{{ (pct * 100).toFixed(1) }}%
                      <span class="demo-bar-count">({{ fmt(demographics.dimensions[dim].counts[cat]) }})</span>
                    </div>
                  </div>
                </div>
              </div>
            </template>
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
      participantsTimeline: [],
      visits: [],
      participationRate: {},
      demographics: {},
      chartMode: 'cumulative',
      sourceMode: 'cumulative',
      dateFrom: '',
      dateTo: '',
      _timelineChart: null,
      _sourceChart: null,
      _categoryChart: null,
    };
  },

  computed: {
    filteredTimeline() {
      return this.timeline.filter(d => {
        if (this.dateFrom && d.date < this.dateFrom) return false;
        if (this.dateTo && d.date > this.dateTo) return false;
        return true;
      });
    },

    cumulativeTimeline() {
      let totals = { surveys: 0, ideas: 0, reactions: 0, total: 0 };
      return this.filteredTimeline.map(d => {
        totals.surveys   += d.surveys;
        totals.ideas     += d.ideas;
        totals.reactions += d.reactions;
        totals.total     += d.total;
        return { date: d.date, ...totals };
      });
    },

    visitsMap() {
      const m = {};
      for (const v of this.visits) m[v.date] = v.visitors;
      return m;
    },

    filteredParticipantsTimeline() {
      return this.participantsTimeline.filter(d => {
        if (this.dateFrom && d.date < this.dateFrom) return false;
        if (this.dateTo && d.date > this.dateTo) return false;
        return true;
      });
    },

    cumulativeParticipantsTimeline() {
      let totals = { confirmed: 0, email_only: 0, anonymous: 0, total: 0 };
      return this.filteredParticipantsTimeline.map(d => {
        totals.confirmed  += d.confirmed;
        totals.email_only += d.email_only;
        totals.anonymous  += d.anonymous;
        totals.total      += d.total;
        return { date: d.date, ...totals };
      });
    },

    demoOrder() {
      return ['political_lean', 'age_bucket', 'race', 'region', 'urban_rural'];
    },
  },

  async mounted() {
    try {
      // Use preloaded data from the app loading screen
      this.participants      = this.preloaded.participants || {};
      this.actions           = this.preloaded.actions || {};
      const tData            = this.preloaded.timeline || {};
      this.timeline          = tData.timeline || [];
      const sData            = this.preloaded.sourceTimeline || {};
      this.participantsTimeline = sData.timeline || [];
      const vData            = this.preloaded.visits || {};
      this.visits            = vData.visits || [];
      this.participationRate = this.preloaded.participationRate || {};
      this.demographics      = this.preloaded.demographics || {};

      // Default date range to full span of data
      if (this.timeline.length) {
        this.dateFrom = this.timeline[0].date;
        this.dateTo   = this.timeline[this.timeline.length - 1].date;
      }

      this.loading = false;
      this.$nextTick(() => {
        this.renderTimelineChart();
        this.renderSourceChart();
        this.renderCategoryChart();
      });
    } catch (e) {
      this.error = 'Failed to load participation data.';
      this.loading = false;
    }
  },

  beforeUnmount() {
    if (this._timelineChart) this._timelineChart.destroy();
    if (this._sourceChart)   this._sourceChart.destroy();
    if (this._categoryChart) this._categoryChart.destroy();
  },

  methods: {
    fmt(n) {
      if (n == null) return '–';
      return Number(n).toLocaleString();
    },

    dimLabel(dim) {
      const labels = {
        political_lean: 'Political Lean',
        age_bucket: 'Age Group',
        race: 'Race / Ethnicity',
        region: 'Region',
        urban_rural: 'Urban / Rural',
      };
      return labels[dim] || dim;
    },

    dimColor(dim) {
      const colors = {
        political_lean: '#6366f1',
        age_bucket: '#0564B8',
        race: '#059669',
        region: '#d97706',
        urban_rural: '#e11d48',
      };
      return colors[dim] || '#003366';
    },

    setMode(mode) {
      this.chartMode = mode;
      this.rebuildChart();
    },

    setSourceMode(mode) {
      this.sourceMode = mode;
      if (this._sourceChart) { this._sourceChart.destroy(); this._sourceChart = null; }
      this.$nextTick(() => this.renderSourceChart());
    },

    resetDates() {
      if (this.timeline.length) {
        this.dateFrom = this.timeline[0].date;
        this.dateTo   = this.timeline[this.timeline.length - 1].date;
      }
      this.rebuildChart();
    },

    rebuildChart() {
      if (this._timelineChart) {
        this._timelineChart.destroy();
        this._timelineChart = null;
      }
      if (this._sourceChart) {
        this._sourceChart.destroy();
        this._sourceChart = null;
      }
      this.$nextTick(() => {
        this.renderTimelineChart();
        this.renderSourceChart();
      });
    },

    renderTimelineChart() {
      const ctx = this.$refs.timelineChart;
      if (!ctx) return;

      const source = this.chartMode === 'cumulative'
        ? this.cumulativeTimeline
        : this.filteredTimeline;

      if (!source.length) return;

      const labels = source.map(d => d.date);
      const isDaily = this.chartMode === 'daily';

      const mkDataset = (label, key, color, fill) => ({
        label,
        data: source.map(d => d[key]),
        borderColor: color,
        backgroundColor: fill,
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.35,
        fill: this.chartMode === 'cumulative' && label === 'Total' ? 'origin' : false,
      });

      // Build visitors dataset from visits data, aligned to the same date labels
      const vm = this.visitsMap;
      let visitorsData;
      if (this.chartMode === 'cumulative') {
        let cumVisits = 0;
        visitorsData = labels.map(d => { cumVisits += (vm[d] || 0); return cumVisits; });
      } else {
        visitorsData = labels.map(d => vm[d] || 0);
      }
      const visitorsDs = {
        type: 'line',
        label: 'Visitors',
        data: visitorsData,
        borderColor: '#e11d48',
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        borderDash: [5, 3],
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.35,
        fill: false,
        yAxisID: 'y1',
      };

      const datasets = isDaily
        ? [
            mkDataset('Surveys',   'surveys',   '#0564B8', 'rgba(5,100,184,.7)'),
            mkDataset('Ideas',     'ideas',     '#059669', 'rgba(5,150,105,.7)'),
            mkDataset('Reactions', 'reactions', '#d97706', 'rgba(217,119,6,.7)'),
            visitorsDs,
          ]
        : [
            mkDataset('Total',     'total',     '#003366', 'rgba(0,51,102,.06)'),
            mkDataset('Surveys',   'surveys',   '#0564B8', 'transparent'),
            mkDataset('Ideas',     'ideas',     '#059669', 'transparent'),
            mkDataset('Reactions', 'reactions', '#d97706', 'transparent'),
            visitorsDs,
          ];

      this._timelineChart = new Chart(ctx, {
        type: isDaily ? 'bar' : 'line',
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          scales: {
            x: {
              grid: { display: false },
              ticks: {
                maxTicksLimit: 10,
                maxRotation: 0,
                color: '#9ca3af',
                font: { size: 11 },
              },
              border: { display: false },
            },
            y: {
              beginAtZero: true,
              ticks: { precision: 0, color: '#9ca3af', font: { size: 11 } },
              grid: { color: 'rgba(0,0,0,.04)' },
              border: { display: false },
              stacked: isDaily,
            },
            y1: {
              position: 'right',
              beginAtZero: true,
              ticks: { precision: 0, color: '#e11d48', font: { size: 11 } },
              grid: { display: false },
              border: { display: false },
              title: { display: true, text: 'Visitors', color: '#e11d48', font: { size: 11 } },
            },
          },
          plugins: {
            legend: {
              position: 'top',
              labels: { boxWidth: 10, padding: 16, font: { size: 12 }, color: '#374151' },
            },
            tooltip: {
              mode: 'index',
              intersect: false,
              backgroundColor: '#003366',
              titleColor: '#C2DFED',
              bodyColor: '#C2DFED',
              padding: 10,
              cornerRadius: 6,
            },
          },
          ...(isDaily && { scales: {
            x: {
              grid: { display: false },
              stacked: true,
              ticks: { maxTicksLimit: 10, maxRotation: 0, color: '#9ca3af', font: { size: 11 } },
              border: { display: false },
            },
            y: {
              beginAtZero: true,
              stacked: true,
              ticks: { precision: 0, color: '#9ca3af', font: { size: 11 } },
              grid: { color: 'rgba(0,0,0,.04)' },
              border: { display: false },
            },
            y1: {
              position: 'right',
              beginAtZero: true,
              stacked: false,
              ticks: { precision: 0, color: '#e11d48', font: { size: 11 } },
              grid: { display: false },
              border: { display: false },
              title: { display: true, text: 'Visitors', color: '#e11d48', font: { size: 11 } },
            },
          }}),
        },
      });
    },

    renderSourceChart() {
      const ctx = this.$refs.sourceChart;
      if (!ctx) return;

      const isDaily = this.sourceMode === 'daily';
      const data = isDaily
        ? this.filteredParticipantsTimeline
        : this.cumulativeParticipantsTimeline;
      if (!data.length) return;

      const labels = data.map(d => d.date);

      const mkDs = (label, key, border, bg) => ({
        label,
        data: data.map(d => d[key] || 0),
        borderColor: border,
        backgroundColor: bg,
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 4,
        tension: 0.35,
        fill: !isDaily && key === 'total' ? 'origin' : false,
      });

      const datasets = isDaily
        ? [
            mkDs('Confirmed',  'confirmed',  '#003366', 'rgba(0,51,102,.7)'),
            mkDs('Email-only', 'email_only', '#0564B8', 'rgba(5,100,184,.7)'),
            mkDs('Anonymous',  'anonymous',  '#9ca3af', 'rgba(156,163,175,.7)'),
          ]
        : [
            mkDs('Total',      'total',      '#003366', 'rgba(0,51,102,.06)'),
            mkDs('Confirmed',  'confirmed',  '#0564B8', 'transparent'),
            mkDs('Email-only', 'email_only', '#36A0E0', 'transparent'),
            mkDs('Anonymous',  'anonymous',  '#9ca3af', 'transparent'),
          ];

      this._sourceChart = new Chart(ctx, {
        type: isDaily ? 'bar' : 'line',
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          scales: {
            x: {
              grid: { display: false },
              stacked: isDaily,
              ticks: { maxTicksLimit: 10, maxRotation: 0, color: '#94a3b8', font: { size: 11 } },
              border: { display: false },
            },
            y: {
              beginAtZero: true,
              stacked: isDaily,
              ticks: { precision: 0, color: '#94a3b8', font: { size: 11 } },
              grid: { color: 'rgba(0,0,0,.05)' },
              border: { display: false },
            },
          },
          plugins: {
            legend: {
              position: 'top',
              labels: { boxWidth: 10, padding: 14, font: { size: 12 }, color: '#374151' },
            },
            tooltip: {
              mode: 'index',
              intersect: false,
              backgroundColor: '#003366',
              titleColor: '#C2DFED',
              bodyColor: '#C2DFED',
              padding: 10,
              cornerRadius: 6,
            },
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
            backgroundColor: ['#003366', '#0564B8', '#C2DFED'],
            borderWidth: 2,
            borderColor: '#fff',
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'right', labels: { boxWidth: 12, font: { size: 12 }, color: '#374151' } },
          },
        },
      });
    },
  },
};
