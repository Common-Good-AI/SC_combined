const { createApp } = Vue;

const DATA_ENDPOINTS = [
  { key: 'participants',       url: '/api/analytics/participants',              label: 'Participants' },
  { key: 'actions',            url: '/api/analytics/actions',                   label: 'Actions' },
  { key: 'actionDistributions', url: '/api/analytics/action-distributions',     label: 'Action distributions' },
  { key: 'timeline',           url: '/api/analytics/participation-timeline',    label: 'Timeline' },
  { key: 'sourceTimeline',     url: '/api/analytics/participation-timeline/by-source', label: 'Source timeline' },
  { key: 'visits',             url: '/api/analytics/visits',                    label: 'Visits' },
  { key: 'combinedViews',      url: '/api/analytics/combined-views',            label: 'Combined views' },
  { key: 'participationRate',  url: '/api/analytics/participation-rate',        label: 'Participation rate' },
  { key: 'ideas',              url: '/api/ideas',                               label: 'Ideas & bridging' },
  { key: 'demographics',       url: '/api/analytics/demographics-baseline',     label: 'Demographics' },
  { key: 'demoCoverage',       url: '/api/analytics/demographic-coverage',      label: 'Demographic coverage' },
  { key: 'themes',             url: '/api/analytics/idea-selections',           label: 'Survey themes' },
  { key: 'tags',               url: '/api/analytics/idea-tags',                 label: 'Idea tags' },
  { key: 'votesByTag',         url: '/api/analytics/votes-by-tag',              label: 'Votes by tag' },
  { key: 'voting',              url: '/api/analytics/voting',                    label: 'Voting results' },
];

const app = createApp({
  data() {
    return {
      activeTab: 'participation',
      // Current user
      user: null,
      // Loading state
      appLoading: true,
      loadError: null,
      loadProgress: 0,       // 0–100
      loadStepLabel: '',
      totalRecords: 0,
      // Pre-fetched data passed to child components
      preloaded: {},
    };
  },

  async mounted() {
    // Remove static pre-loader now that Vue has mounted
    document.getElementById('pre-loader')?.remove();

    try {
      // Step 0: Fetch current user
      const meRes = await fetch('/api/me');
      if (meRes.ok) this.user = await meRes.json();

      // Step 1: Wait for backend data to finish loading
      this.loadStepLabel = 'Server is loading data…';
      await this._waitForBackendReady();

      // Step 2: Fetch summary to get record counts
      this.loadStepLabel = 'Connecting to server…';
      const summaryRes = await fetch('/api/data/summary');
      if (!summaryRes.ok) throw new Error('Server unavailable');
      const summary = await summaryRes.json();
      const counts = summary.row_counts || {};
      this.totalRecords = Object.values(counts).reduce((s, n) => s + n, 0);
      this.loadProgress = 5;

      // Step 3: Fetch each endpoint sequentially to show progress
      const stepSize = 95 / DATA_ENDPOINTS.length;
      for (let i = 0; i < DATA_ENDPOINTS.length; i++) {
        const ep = DATA_ENDPOINTS[i];
        this.loadStepLabel = ep.label;
        const res = await fetch(ep.url);
        if (!res.ok) throw new Error(`Failed to load ${ep.label}`);
        this.preloaded[ep.key] = await res.json();
        this.loadProgress = Math.round(5 + stepSize * (i + 1));
      }

      this.loadProgress = 100;
      // Brief pause so user sees 100%
      await new Promise(r => setTimeout(r, 300));
      this.appLoading = false;
    } catch (e) {
      this.loadError = e.message || 'Failed to load data';
    }
  },

  methods: {
    async _waitForBackendReady() {
      // Poll /api/loading-status until data is loaded (or errored)
      const MAX_WAIT_MS = 5 * 60 * 1000; // 5 minutes
      const start = Date.now();
      while (true) {
        try {
          const res = await fetch('/api/loading-status');
          if (res.ok) {
            const info = await res.json();
            if (info.status === 'loaded' || info.status === 'loaded_with_errors') return;
            if (info.status === 'error' || info.status === 'config_error') {
              const detail = (info.errors && info.errors.length) ? ': ' + info.errors[0] : '';
              throw new Error('Server failed to load data' + detail);
            }
            this.loadStepLabel = `Server is loading data… (${info.tables_loaded} tables ready)`;
          }
        } catch (e) {
          if (e.message.includes('Server failed')) throw e;
        }
        if (Date.now() - start > MAX_WAIT_MS) {
          throw new Error('Server took too long to load data. Please try refreshing.');
        }
        await new Promise(r => setTimeout(r, 2000));
      }
    },
  },
});

// Register components
app.component('summary-tab', SummaryTab);
app.component('participation-tab', ParticipationTab);
app.component('ideas-tab', IdeasTab);
app.component('voting-tab', VotingTab);
app.component('idea-detail', IdeaDetail);

app.mount('#app');
