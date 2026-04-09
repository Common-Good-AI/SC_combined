const VotingTab = {
  props: {
    preloaded: { type: Object, default: () => ({}) },
  },
  template: `
    <div>
      <div v-if="loading" class="loading">Loading voting data…</div>
      <div v-else-if="error" class="error">{{ error }}</div>
      <template v-else>

        <!-- Summary cards -->
        <div class="card-row">
          <div class="card">
            <div class="label">Total Voters</div>
            <div class="value">{{ fmt(votingData.total_voters) }}</div>
          </div>
          <div class="card">
            <div class="label">Total Votes Cast</div>
            <div class="value">{{ fmt(votingData.total_votes) }}</div>
          </div>
          <div class="card">
            <div class="label">Issues</div>
            <div class="value">{{ votingData.issues ? votingData.issues.length : 0 }}</div>
          </div>
          <div class="card">
            <div class="label">Votes Per Voter</div>
            <div class="value">{{ votingData.total_voters ? (votingData.total_votes / votingData.total_voters).toFixed(1) : '–' }}</div>
          </div>
        </div>

        <!-- Issue Rankings -->
        <div style="margin-top:24px;">
          <h3 class="section-title">Issue Rankings by Vote Percentage</h3>
          <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:12px;">
            Percentage of voters who voted for each issue (each voter selects up to 6).
          </p>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th style="width:40px">#</th>
                  <th>Issue</th>
                  <th style="width:100px; text-align:right">Voters</th>
                  <th style="width:200px">% of Voters</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(issue, idx) in votingData.issues" :key="issue.idea_id">
                  <td style="color:#94a3b8;">{{ idx + 1 }}</td>
                  <td>{{ issue.title }}</td>
                  <td style="text-align:right">{{ issue.voters }}</td>
                  <td>
                    <div style="display:flex; align-items:center; gap:8px;">
                      <div style="flex:1; background:#1e293b; border-radius:4px; height:18px; overflow:hidden;">
                        <div :style="{
                          width: issue.vote_pct + '%',
                          height: '100%',
                          background: barColor(idx),
                          borderRadius: '4px',
                          transition: 'width 0.6s ease'
                        }"></div>
                      </div>
                      <span style="min-width:50px; text-align:right; font-size:0.85rem; font-weight:600;">
                        {{ issue.vote_pct }}%
                      </span>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Top-X Coverage -->
        <div style="margin-top:32px;">
          <h3 class="section-title">Top-X Issue Coverage</h3>
          <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:12px;">
            What percentage of voters have at least <strong style="color:#c084fc;">Y</strong> issues
            in the top <strong style="color:#38bdf8;">Z</strong> most-voted issues?
          </p>
          <div style="display:flex; align-items:center; gap:16px; margin-bottom:8px; flex-wrap:wrap;">
            <label style="font-size:0.9rem; color:#e2e8f0;">Top Z issues:</label>
            <input type="range" v-model.number="topX" :min="1" :max="maxX"
                   style="flex:0 0 180px; cursor:pointer;">
            <span style="font-size:1.1rem; font-weight:700; color:#38bdf8; min-width:30px;">{{ topX }}</span>
          </div>
          <div style="display:flex; align-items:center; gap:16px; margin-bottom:16px; flex-wrap:wrap;">
            <label style="font-size:0.9rem; color:#e2e8f0;">At least Y:</label>
            <input type="range" v-model.number="minY" :min="1" :max="topX"
                   style="flex:0 0 180px; cursor:pointer;">
            <span style="font-size:1.1rem; font-weight:700; color:#c084fc; min-width:30px;">{{ minY }}</span>
            <span v-if="topXLoading" style="color:#94a3b8; font-size:0.85rem;">Loading…</span>
          </div>

          <div class="card-row" v-if="topXData">
            <div class="card">
              <div class="label">Coverage</div>
              <div class="value" style="color:#22c55e;">{{ topXData.coverage_pct }}%</div>
              <div class="sub">
                {{ topXData.voters_with_match }} of {{ topXData.voters_total }} voters
                have at least {{ minY }} issue{{ minY > 1 ? 's' : '' }} in the top {{ topX }}
              </div>
            </div>
          </div>

          <div v-if="topXData && topXData.top_ideas && topXData.top_ideas.length" class="table-container" style="margin-top:12px;">
            <table>
              <thead>
                <tr>
                  <th style="width:40px">#</th>
                  <th>Issue (Top {{ topX }})</th>
                  <th style="width:100px; text-align:right">Voters</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(idea, idx) in topXData.top_ideas" :key="idea.idea_id">
                  <td style="color:#94a3b8;">{{ idx + 1 }}</td>
                  <td>{{ idea.title }}</td>
                  <td style="text-align:right">{{ idea.voters }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Demographics -->
        <div style="margin-top:32px;" v-if="demoData">
          <h3 class="section-title">Voter Demographics</h3>
          <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:12px;">
            Demographic breakdown of the {{ demoData.total_voters }} voters in this phase.
            Demographics known for {{ demoData.voters_with_demographics }} voters.
          </p>

          <div class="card-row" style="flex-wrap:wrap;">
            <div v-for="(dim, dimKey) in demoData.demographics" :key="dimKey"
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
                    background: '#6366f1',
                    borderRadius: '4px',
                    transition: 'width 0.6s ease'
                  }"></div>
                </div>
              </div>
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
      votingData: {},
      topX: 6,
      minY: 1,
      topXData: null,
      topXLoading: false,
      demoData: null,
      _debounceTimer: null,
    };
  },

  computed: {
    maxX() {
      return this.votingData.issues ? this.votingData.issues.length : 25;
    },
  },

  watch: {
    topX(newVal) {
      // Clamp minY so it can't exceed topX
      if (this.minY > newVal) this.minY = newVal;
      clearTimeout(this._debounceTimer);
      this._debounceTimer = setTimeout(() => this.fetchTopX(), 250);
    },
    minY() {
      clearTimeout(this._debounceTimer);
      this._debounceTimer = setTimeout(() => this.fetchTopX(), 250);
    },
  },

  async mounted() {
    try {
      if (this.preloaded.voting) {
        this.votingData = this.preloaded.voting;
      } else {
        const res = await fetch('/api/analytics/voting');
        if (!res.ok) throw new Error('Failed to load voting data');
        this.votingData = await res.json();
      }

      // Fetch top-X and demographics in parallel
      await Promise.all([
        this.fetchTopX(),
        this.fetchDemographics(),
      ]);

      this.loading = false;
    } catch (e) {
      this.error = e.message || 'Failed to load voting data';
      this.loading = false;
    }
  },

  methods: {
    fmt(n) {
      if (n == null) return '–';
      return Number(n).toLocaleString();
    },

    barColor(idx) {
      const colors = ['#3b82f6', '#6366f1', '#8b5cf6', '#a78bfa', '#818cf8'];
      return colors[idx % colors.length];
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

    async fetchTopX() {
      this.topXLoading = true;
      try {
        const res = await fetch(`/api/analytics/voting/top-x?x=${this.topX}&y=${this.minY}`);
        if (res.ok) this.topXData = await res.json();
      } catch (e) {
        console.error('Top-X fetch error:', e);
      }
      this.topXLoading = false;
    },

    async fetchDemographics() {
      try {
        const res = await fetch('/api/analytics/voting/demographics');
        if (res.ok) this.demoData = await res.json();
      } catch (e) {
        console.error('Demographics fetch error:', e);
      }
    },
  },
};
