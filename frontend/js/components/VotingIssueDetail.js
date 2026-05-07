const VotingIssueDetail = {
  props: {
    ideaId: { type: String, required: true },
  },
  emits: ['close'],
  template: `
    <div class="modal-overlay" @click.self="$emit('close')">
      <div class="modal">
        <div v-if="loading" class="loading">Loading issue demographics…</div>
        <div v-else-if="error" class="error">{{ error }}</div>
        <template v-else>
          <div class="modal-header">
            <h2>{{ data.title }}</h2>
            <button class="close-btn" @click="$emit('close')">&times;</button>
          </div>

          <!-- Summary cards -->
          <div class="stat-grid">
            <div class="stat-card">
              <div class="stat-value">{{ fmt(data.total_voters) }}</div>
              <div class="stat-label">Voters for this Issue</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">{{ fmt(data.voters_with_demographics) }}</div>
              <div class="stat-label">Voters with Demographics</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">{{ coveragePct }}%</div>
              <div class="stat-label">Demographic Coverage</div>
            </div>
          </div>

          <!-- Demographic breakdowns -->
          <div v-if="data.demographics" style="margin-top:24px;">
            <h3 class="section-title">Voter Demographics</h3>
            <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:12px;">
              Demographic breakdown of the {{ data.total_voters }} voters who selected this issue.
            </p>

            <div class="card-row" style="flex-wrap:wrap;">
              <div v-for="(dim, dimKey) in data.demographics" :key="dimKey"
                   class="card" style="flex:1 1 320px; min-width:300px;">
                <div class="label">{{ dimLabel(dimKey) }}</div>
                <div class="sub" style="margin-bottom:8px;">{{ dim.known }} voters with data</div>
                <div v-for="group in dim.groups" :key="group.label"
                     style="margin-bottom:6px;">
                  <div style="display:flex; justify-content:space-between; font-size:0.85rem; margin-bottom:2px;">
                    <span>{{ group.label }}</span>
                    <span style="font-weight:600;">{{ group.pct }}% ({{ group.count }})</span>
                  </div>
                  <div style="background:#1e293b; border-radius:4px; height:14px; overflow:hidden;">
                    <div :style="{
                      width: group.pct + '%',
                      height: '100%',
                      background: dimColor(dimKey),
                      borderRadius: '4px',
                      transition: 'width 0.6s ease'
                    }"></div>
                  </div>
                </div>
                <div v-if="!dim.groups || dim.groups.length === 0"
                     style="color:#64748b; font-size:0.85rem; font-style:italic;">
                  No data available
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  `,

  data() {
    return {
      loading: true,
      error: null,
      data: null,
    };
  },

  computed: {
    coveragePct() {
      if (!this.data || !this.data.total_voters) return 0;
      return Math.round(this.data.voters_with_demographics / this.data.total_voters * 100);
    },
  },

  async mounted() {
    try {
      const res = await fetch(`/api/analytics/voting/issue/${encodeURIComponent(this.ideaId)}/demographics`);
      if (!res.ok) throw new Error('Failed to load issue demographics');
      this.data = await res.json();
    } catch (e) {
      this.error = e.message || 'Failed to load demographics';
    }
    this.loading = false;
  },

  methods: {
    fmt(n) {
      if (n == null) return '–';
      return Number(n).toLocaleString();
    },

    dimLabel(key) {
      const labels = {
        age_bucket: 'Age',
        race: 'Race / Ethnicity',
        political_lean: 'Political Lean',
        region: 'Region',
      };
      return labels[key] || key;
    },

    dimColor(key) {
      const colors = {
        age_bucket: '#3b82f6',
        race: '#8b5cf6',
        political_lean: '#6366f1',
        region: '#06b6d4',
      };
      return colors[key] || '#6366f1';
    },
  },
};
