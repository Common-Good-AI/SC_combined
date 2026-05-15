const VotingTab = {
  props: {
    preloaded: { type: Object, default: () => ({}) },
  },
  template: `
    <div>
      <div v-if="loading" class="loading">Loading voting data…</div>
      <div v-else-if="error" class="error">{{ error }}</div>
      <template v-else>

        <!-- Demographic "What-If" Filter -->
        <div data-filter-panel style="background:#1e293b; border-radius:8px; padding:16px 20px; margin-bottom:20px;">
          <div style="display:flex; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap;">
            <span style="font-size:0.95rem; font-weight:600; color:#e2e8f0;">What if only these groups voted?</span>
            <span v-if="filterLoading" style="color:#94a3b8; font-size:0.85rem;">Loading…</span>
            <button v-if="hasActiveFilters" @click="clearAllFilters"
                    style="margin-left:auto; background:#4f46e5; color:#fff; border:none; border-radius:4px; padding:4px 12px; font-size:0.8rem; cursor:pointer;">
              Clear All
            </button>
          </div>
          <div style="display:flex; gap:12px; flex-wrap:wrap;">
            <div v-for="(dim, dimKey) in demographicGroups" :key="dimKey"
                 style="position:relative; flex:1; min-width:160px;">
              <button @click="toggleDropdown(dimKey)"
                      :style="{
                        width: '100%', textAlign: 'left', background: '#0f172a',
                        color: selectedFilters[dimKey] && selectedFilters[dimKey].length ? '#a5b4fc' : '#e2e8f0',
                        border: selectedFilters[dimKey] && selectedFilters[dimKey].length ? '1px solid #6366f1' : '1px solid #334155',
                        borderRadius: '6px', padding: '8px 12px', fontSize: '0.85rem', cursor: 'pointer'
                      }">
                {{ dim.label }}
                <span v-if="selectedFilters[dimKey] && selectedFilters[dimKey].length"
                      style="background:#6366f1; color:#fff; border-radius:10px; padding:1px 6px; font-size:0.75rem; margin-left:6px;">
                  {{ selectedFilters[dimKey].length }}
                </span>
                <span style="float:right;">▾</span>
              </button>
              <div v-if="openDropdown === dimKey"
                   style="position:absolute; top:100%; left:0; right:0; z-index:50; background:#0f172a; border:1px solid #334155; border-radius:6px; margin-top:4px; max-height:220px; overflow-y:auto; box-shadow:0 8px 24px rgba(0,0,0,0.4);">
                <label v-for="g in dim.groups" :key="g"
                       style="display:flex; align-items:center; gap:8px; padding:6px 12px; cursor:pointer; font-size:0.85rem; color:#e2e8f0;"
                       @mouseenter="$event.target.style.background='#1e293b'"
                       @mouseleave="$event.target.style.background='transparent'">
                  <input type="checkbox" :value="g"
                         :checked="selectedFilters[dimKey] && selectedFilters[dimKey].includes(g)"
                         @change="toggleFilter(dimKey, g)"
                         style="accent-color:#6366f1; cursor:pointer;">
                  {{ g }}
                </label>
              </div>
            </div>
          </div>
        </div>

        <!-- Active filter banner -->
        <div v-if="hasActiveFilters && votingData.filter_info"
             style="background:#312e81; border:1px solid #4f46e5; border-radius:6px; padding:10px 16px; margin-bottom:16px; font-size:0.85rem; color:#c7d2fe; display:flex; align-items:center; flex-wrap:wrap; gap:8px;">
          <span>Showing results for:</span>
          <template v-for="(groups, dimKey) in activeFilterSummary" :key="dimKey">
            <span style="background:#4f46e5; color:#fff; border-radius:4px; padding:2px 8px; font-size:0.8rem;">
              {{ dimLabel(dimKey) }}: {{ groups.join(', ') }}
            </span>
          </template>
          <span style="margin-left:4px;">— {{ fmt(votingData.filter_info.filtered_voters) }} voters (of {{ fmt(unfilteredTotal) }} total)</span>
        </div>

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
                <tr v-for="(issue, idx) in votingData.issues" :key="issue.idea_id"
                    @click="selectedIssueId = issue.idea_id"
                    class="clickable">
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

        <!-- Surveys -->
        <div style="margin-top:32px;" v-if="surveyData">
          <h3 class="section-title">Surveys</h3>
          <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:12px;">
            Partial and completed response counts for each survey.
          </p>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>Survey</th>
                  <th style="width:120px; text-align:right">Completed</th>
                  <th style="width:120px; text-align:right">Partial</th>
                  <th style="width:120px; text-align:right">Total</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in surveyData" :key="s.form_id">
                  <td>{{ s.title }}</td>
                  <td style="text-align:right">{{ fmt(s.completed) }}</td>
                  <td style="text-align:right">{{ fmt(s.partial) }}</td>
                  <td style="text-align:right; font-weight:600;">{{ fmt(s.total) }}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr style="border-top:2px solid #334155; font-weight:700;">
                  <td>All Surveys</td>
                  <td style="text-align:right">{{ fmt(surveyTotals.completed) }}</td>
                  <td style="text-align:right">{{ fmt(surveyTotals.partial) }}</td>
                  <td style="text-align:right">{{ fmt(surveyTotals.total) }}</td>
                </tr>
              </tfoot>
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

        <!-- Issue detail modal -->
        <voting-issue-detail
          v-if="selectedIssueId"
          :idea-id="selectedIssueId"
          @close="selectedIssueId = null"
        />

      </template>
    </div>
  `,

  data() {
    return {
      loading: true,
      error: null,
      votingData: {},
      selectedIssueId: null,
      topX: 6,
      minY: 1,
      topXData: null,
      topXLoading: false,
      demoData: null,
      surveyData: null,
      _debounceTimer: null,
      // Demographic "What-If" multi-filter
      demographicGroups: {},
      selectedFilters: {},  // { dimKey: [group1, group2], ... }
      openDropdown: null,
      filterLoading: false,
      unfilteredTotal: 0,
    };
  },

  computed: {
    maxX() {
      return this.votingData.issues ? this.votingData.issues.length : 25;
    },
    surveyTotals() {
      if (!this.surveyData) return { completed: 0, partial: 0, total: 0 };
      return this.surveyData.reduce((acc, s) => ({
        completed: acc.completed + s.completed,
        partial: acc.partial + s.partial,
        total: acc.total + s.total,
      }), { completed: 0, partial: 0, total: 0 });
    },
    hasActiveFilters() {
      return Object.values(this.selectedFilters).some(arr => arr && arr.length > 0);
    },
    activeFilterSummary() {
      const summary = {};
      for (const [dim, groups] of Object.entries(this.selectedFilters)) {
        if (groups && groups.length > 0) summary[dim] = groups;
      }
      return summary;
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
    // Close dropdown on outside clicks
    this._outsideClickHandler = (e) => {
      if (this.openDropdown && !e.target.closest('[data-filter-panel]')) {
        this.openDropdown = null;
      }
    };
    document.addEventListener('click', this._outsideClickHandler);

    try {
      if (this.preloaded.voting) {
        this.votingData = this.preloaded.voting;
      } else {
        const res = await fetch('/api/analytics/voting');
        if (!res.ok) throw new Error('Failed to load voting data');
        this.votingData = await res.json();
      }
      this.unfilteredTotal = this.votingData.total_voters;

      // Fetch top-X, demographics, surveys, and demographic groups in parallel
      await Promise.all([
        this.fetchTopX(),
        this.fetchDemographics(),
        this.fetchSurveys(),
        this.fetchDemographicGroups(),
      ]);

      this.loading = false;
    } catch (e) {
      this.error = e.message || 'Failed to load voting data';
      this.loading = false;
    }
  },

  beforeUnmount() {
    if (this._outsideClickHandler) {
      document.removeEventListener('click', this._outsideClickHandler);
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
        let url = `/api/analytics/voting/top-x?x=${this.topX}&y=${this.minY}`;
        const filterQS = this.buildFilterQuery();
        if (filterQS) url += '&' + filterQS;
        const res = await fetch(url);
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

    async fetchSurveys() {
      try {
        const res = await fetch('/api/analytics/voting/surveys');
        if (res.ok) this.surveyData = await res.json();
      } catch (e) {
        console.error('Surveys fetch error:', e);
      }
    },

    async fetchDemographicGroups() {
      try {
        const res = await fetch('/api/analytics/voting/demographic-groups');
        if (res.ok) {
          const data = await res.json();
          this.demographicGroups = data.dimensions || {};
        }
      } catch (e) {
        console.error('Demographic groups fetch error:', e);
      }
    },

    buildFilterQuery() {
      const params = new URLSearchParams();
      for (const [dim, groups] of Object.entries(this.selectedFilters)) {
        if (groups && groups.length) {
          for (const g of groups) {
            params.append(dim, g);
          }
        }
      }
      return params.toString();
    },

    toggleDropdown(dimKey) {
      this.openDropdown = this.openDropdown === dimKey ? null : dimKey;
    },

    toggleFilter(dimKey, group) {
      if (!this.selectedFilters[dimKey]) {
        this.selectedFilters[dimKey] = [];
      }
      const idx = this.selectedFilters[dimKey].indexOf(group);
      if (idx >= 0) {
        this.selectedFilters[dimKey].splice(idx, 1);
      } else {
        this.selectedFilters[dimKey].push(group);
      }
      this.applyFilter();
    },

    clearAllFilters() {
      for (const dim of Object.keys(this.selectedFilters)) {
        this.selectedFilters[dim] = [];
      }
      this.openDropdown = null;
      this.applyFilter();
    },

    async applyFilter() {
      this.filterLoading = true;
      try {
        let url = '/api/analytics/voting';
        const filterQS = this.buildFilterQuery();
        if (filterQS) url += '?' + filterQS;
        const res = await fetch(url);
        if (res.ok) this.votingData = await res.json();
        await this.fetchTopX();
      } catch (e) {
        console.error('Filter apply error:', e);
      }
      this.filterLoading = false;
    },
  },
};
