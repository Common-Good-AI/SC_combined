const { createApp } = Vue;

const DATA_ENDPOINTS = [
  { key: 'participants',       url: '/api/analytics/participants',              label: 'Participants' },
  { key: 'actions',            url: '/api/analytics/actions',                   label: 'Actions' },
  { key: 'timeline',           url: '/api/analytics/participation-timeline',    label: 'Timeline' },
  { key: 'sourceTimeline',     url: '/api/analytics/participation-timeline/by-source', label: 'Source timeline' },
  { key: 'ideas',              url: '/api/ideas',                               label: 'Ideas & bridging' },
  { key: 'themes',             url: '/api/analytics/idea-selections',           label: 'Survey themes' },
];

const app = createApp({
  data() {
    return {
      activeTab: 'participation',
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
    try {
      // Step 1: Fetch summary to get record counts
      this.loadStepLabel = 'Connecting to server…';
      const summaryRes = await fetch('/api/data/summary');
      if (!summaryRes.ok) throw new Error('Server unavailable');
      const summary = await summaryRes.json();
      const counts = summary.row_counts || {};
      this.totalRecords = Object.values(counts).reduce((s, n) => s + n, 0);
      this.loadProgress = 5;

      // Step 2: Fetch each endpoint sequentially to show progress
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
});

// Register components
app.component('participation-tab', ParticipationTab);
app.component('ideas-tab', IdeasTab);
app.component('idea-detail', IdeaDetail);

app.mount('#app');
