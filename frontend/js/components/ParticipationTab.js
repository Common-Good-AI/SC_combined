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
          <div class="card">
            <div class="label">Comments</div>
            <div class="value">{{ fmt(actions.comments) }}</div>
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

        <!-- GoVocal Visitors -->
        <div class="card-row" v-if="combinedViews && combinedViews.govocal">
          <div class="card">
            <div class="label">GoVocal Visitors</div>
            <div class="value">{{ fmt(combinedViews.govocal.visitors) }}</div>
            <div class="sub">
              {{ fmt(combinedViews.govocal.page_loads) }} page loads
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

        <!-- Action distribution charts -->
        <div class="card-row" v-if="actionDistributions" style="display:grid;grid-template-columns:repeat(3,1fr);gap:1.25rem">
          <div class="chart-container" style="margin:0">
            <h3>Reactions Distribution</h3>
            <p style="color:#64748b;font-size:.82em;margin:-.25em 0 .75em">
              {{ fmt(actionDistributions.reactions.total_users) }} users ·
              median {{ fmt(actionDistributions.reactions.median_actions) }} ·
              mean {{ actionDistributions.reactions.mean_actions }}
            </p>
            <div class="chart-wrap" style="height:240px">
              <canvas ref="reactionsDistChart"></canvas>
            </div>
          </div>
          <div class="chart-container" style="margin:0">
            <h3>Ideas Distribution</h3>
            <p style="color:#64748b;font-size:.82em;margin:-.25em 0 .75em">
              {{ fmt(actionDistributions.ideas.total_users) }} users ·
              median {{ fmt(actionDistributions.ideas.median_actions) }} ·
              mean {{ actionDistributions.ideas.mean_actions }}
            </p>
            <div class="chart-wrap" style="height:240px">
              <canvas ref="ideasDistChart"></canvas>
            </div>
          </div>
          <div class="chart-container" style="margin:0">
            <h3>Comments Distribution</h3>
            <p style="color:#64748b;font-size:.82em;margin:-.25em 0 .75em">
              {{ fmt(actionDistributions.comments.total_users) }} users ·
              median {{ fmt(actionDistributions.comments.median_actions) }} ·
              mean {{ actionDistributions.comments.mean_actions }}
            </p>
            <div class="chart-wrap" style="height:240px">
              <canvas ref="commentsDistChart"></canvas>
            </div>
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
      combinedViews: null,
      participationRate: {},
      demographics: {},
      actionDistributions: { reactions: {}, ideas: {}, comments: {} },
      chartMode: 'cumulative',
      sourceMode: 'cumulative',
      dateFrom: '',
      dateTo: '',
      _timelineChart: null,
      _sourceChart: null,
      _categoryChart: null,
      _reactionsDistChart: null,
      _ideasDistChart: null,
      _commentsDistChart: null,
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
      this.combinedViews     = this.preloaded.combinedViews || null;
      this.participationRate = this.preloaded.participationRate || {};
      this.demographics      = this.preloaded.demographics || {};
      this.actionDistributions = this.preloaded.actionDistributions || { reactions: {}, ideas: {}, comments: {} };

      // Default date range to full span of BOTH timelines so participants
      // whose first-seen date predates the first recorded action are included.
      const allDates = [
        ...this.timeline.map(d => d.date),
        ...this.participantsTimeline.map(d => d.date),
      ].sort();
      if (allDates.length) {
        this.dateFrom = allDates[0];
        this.dateTo   = allDates[allDates.length - 1];
      }

      this.loading = false;
      this.$nextTick(() => {
        this.renderTimelineChart();
        this.renderSourceChart();
        this.renderCategoryChart();
        this.renderDistributionCharts();
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
    if (this._reactionsDistChart) this._reactionsDistChart.destroy();
    if (this._ideasDistChart)     this._ideasDistChart.destroy();
    if (this._commentsDistChart)  this._commentsDistChart.destroy();
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
      if (this._timelineChart) { this._timelineChart.destroy(); this._timelineChart = null; }

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
      if (this._sourceChart) { this._sourceChart.destroy(); this._sourceChart = null; }

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

    renderDistributionCharts() {
      const configs = [
        { ref: 'reactionsDistChart', key: '_reactionsDistChart', data: this.actionDistributions.reactions, color: '#d97706', label: 'Reactions' },
        { ref: 'ideasDistChart',     key: '_ideasDistChart',     data: this.actionDistributions.ideas,     color: '#059669', label: 'Ideas' },
        { ref: 'commentsDistChart',  key: '_commentsDistChart',  data: this.actionDistributions.comments,  color: '#0564B8', label: 'Comments' },
      ];
      for (const cfg of configs) {
        const ctx = this.$refs[cfg.ref];
        if (!ctx) continue;
        if (this[cfg.key]) { this[cfg.key].destroy(); this[cfg.key] = null; }
        const dist = cfg.data;
        if (!dist || !dist.percentiles || !dist.percentiles.length) continue;

        this[cfg.key] = new Chart(ctx, {
          type: 'line',
          data: {
            labels: dist.percentiles.map(p => p + '%'),
            datasets: [{
              label: cfg.label + ' per user',
              data: dist.counts,
              borderColor: cfg.color,
              backgroundColor: cfg.color + '18',
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 4,
              tension: 0.3,
              fill: 'origin',
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
              x: {
                title: { display: true, text: 'User Percentile', color: '#64748b', font: { size: 11 } },
                grid: { display: false },
                ticks: {
                  maxTicksLimit: 6,
                  color: '#94a3b8',
                  font: { size: 10 },
                  callback: function(val, idx) {
                    const label = this.getLabelForValue(idx);
                    return ['0%','20%','40%','60%','80%','100%'].includes(label) ? label : '';
                  },
                },
                border: { display: false },
              },
              y: {
                title: { display: true, text: '# of Actions', color: '#64748b', font: { size: 11 } },
                beginAtZero: true,
                ticks: { precision: 0, color: '#94a3b8', font: { size: 10 } },
                grid: { color: 'rgba(0,0,0,.04)' },
                border: { display: false },
              },
            },
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: '#003366',
                titleColor: '#C2DFED',
                bodyColor: '#C2DFED',
                padding: 8,
                cornerRadius: 6,
                callbacks: {
                  title: (items) => 'Percentile: ' + items[0].label,
                  label: (item) => item.parsed.y + ' ' + cfg.label.toLowerCase(),
                },
              },
            },
          },
        });
      }
    },

    renderCategoryChart() {
      const ctx = this.$refs.categoryChart;
      if (!ctx) return;
      if (this._categoryChart) { this._categoryChart.destroy(); this._categoryChart = null; }

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
