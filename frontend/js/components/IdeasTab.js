const IdeasTab = {
  props: {
    preloaded: { type: Object, default: () => ({}) },
  },
  template: `
    <div>
      <div v-if="loading" class="loading">Loading ideas data…</div>
      <div v-else-if="error" class="error">{{ error }}</div>
      <template v-else>

        <!-- Fixed-position bridging tooltip -->
        <div v-if="tooltip.visible"
             class="fixed-tooltip"
             :style="{ top: tooltip.y + 'px', left: tooltip.x + 'px' }">
          {{ tooltip.text }}
        </div>

        <!-- Demographic coverage rates -->
        <div v-if="demoCoverage.length" class="demo-coverage-container" style="margin-bottom:24px;">
          <h3 class="section-title">Demographic Data Coverage</h3>
          <p style="color:#94a3b8; font-size:0.85rem; margin-bottom:8px;">
            Percentage of unique users per action type for whom we have demographic data.
          </p>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>Action</th>
                  <th style="width:130px">Total Users</th>
                  <th style="width:170px">Users with Demographics</th>
                  <th style="width:130px">Coverage</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in demoCoverage" :key="row.action">
                  <td>{{ row.action }}</td>
                  <td>{{ row.total_users.toLocaleString() }}</td>
                  <td>{{ row.users_with_demo.toLocaleString() }}</td>
                  <td>
                    <span :class="coverageClass(row.coverage_pct)">
                      {{ row.coverage_pct }}%
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Popular themes from surveys -->
        <div class="themes-chart-container">
          <h3>Most Popular Themes from Surveys</h3>
          <p v-if="!themes.selections || !themes.selections.length"
             style="color:#94a3b8; font-size:0.9rem;">No theme data available.</p>
          <div v-else class="themes-chart-wrap"
               :style="{ height: Math.max(200, themes.selections.length * 36) + 'px' }">
            <canvas ref="themesChart"></canvas>
          </div>
        </div>

        <!-- Ideas table -->
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:4px;">
          <h3 class="section-title" style="margin-bottom:0">Ideas ({{ sortedIdeas.length }})</h3>
          <div style="display:flex; align-items:center; gap:6px; margin-left:auto;">
            <label style="font-size:0.82rem; color:#94a3b8;">Score method:</label>
            <select v-model="scoringMethod" style="font-size:0.82rem; padding:2px 6px; border-radius:4px; border:1px solid #334155; background:#1e293b; color:#e2e8f0;">
              <option value="jsd">JSD (Diversity)</option>
              <option value="wmga">WMGA (Group Approval)</option>
            </select>
            <label style="font-size:0.82rem; color:#94a3b8; margin-left:8px; display:flex; align-items:center; gap:4px; cursor:pointer;">
              <input type="checkbox" v-model="polarizationPenalty" style="cursor:pointer;">
              Polarization Penalty
            </label>
          </div>
          <button class="export-btn" @click="exportCSV" title="Download top 100 ideas as CSV">
            &#x2B07; Export CSV
          </button>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th style="width:60px">Rank</th>
                <th @click="sortBy('title')">
                  Title <span class="sort-arrow">{{ sortArrow('title') }}</span>
                </th>
                <th @click="sortBy('likes')" style="width:100px">
                  Likes <span class="sort-arrow">{{ sortArrow('likes') }}</span>
                </th>
                <th @click="sortBy('dislikes')" style="width:100px">
                  Dislikes <span class="sort-arrow">{{ sortArrow('dislikes') }}</span>
                </th>
                <th @click="sortBy('approval')" style="width:130px">
                  Approval Ratio
                  <span class="info-icon"
                        @mouseenter="showTooltip($event, 'Percentage of reactions that are likes: Likes ÷ (Likes + Dislikes). Higher = more broadly approved.')"
                        @mouseleave="hideTooltip"
                        @click.stop>
                    &#9432;
                  </span>
                  <span class="sort-arrow">{{ sortArrow('approval') }}</span>
                </th>
                <th @click="sortBy('bridging')" style="width:160px">
                  Consensus Score
                  <span class="info-icon"
                        @mouseenter="showTooltip($event, 'Measures how broadly an idea is supported across demographic groups (0-100). Factors in approval ratio (likes vs dislikes), engagement volume (more reactions = higher weight), demographic diversity of support (Political Lean 50%, Urban/Rural 20%, Age 10%, Race 10%, Region 10%), and engagement level. Higher = wider cross-group appeal with strong approval and participation.')"
                        @mouseleave="hideTooltip"
                        @click.stop>
                    &#9432;
                  </span>
                  <span class="sort-arrow">{{ sortArrow('bridging') }}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(idea, idx) in paginatedIdeas" :key="idea.idea_id"
                  class="clickable" @click="selectIdea(idea.idea_id)">
                <td style="color:#64748b; font-weight:500">{{ (currentPage - 1) * pageSize + idx + 1 }}</td>
                <td>{{ idea.title || '(Untitled)' }}</td>
                <td>{{ idea.reactions.upvotes }}</td>
                <td>{{ idea.reactions.downvotes }}</td>
                <td>
                  <span :class="approvalClass(idea)">
                    {{ approvalLabel(idea) }}
                  </span>
                </td>
                <td>
                  <span :class="bridgingClass(idea)">
                    {{ bridgingLabel(idea) }}
                  </span>
                </td>
              </tr>
              <tr v-if="!paginatedIdeas.length">
                <td colspan="6" style="text-align:center; color:#94a3b8">No ideas found.</td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Pagination -->
        <div v-if="totalPages > 1" class="pagination">
          <button :disabled="currentPage <= 1" @click="currentPage = 1">&laquo;</button>
          <button :disabled="currentPage <= 1" @click="currentPage--">&lsaquo;</button>
          <span class="page-info">Page {{ currentPage }} of {{ totalPages }}</span>
          <button :disabled="currentPage >= totalPages" @click="currentPage++">&rsaquo;</button>
          <button :disabled="currentPage >= totalPages" @click="currentPage = totalPages">&raquo;</button>
        </div>

        <!-- Idea detail modal -->
        <idea-detail
          v-if="selectedIdeaId"
          :idea-id="selectedIdeaId"
          :scoring-method="scoringMethod"
          :polarization-penalty="polarizationPenalty"
          @close="selectedIdeaId = null"
        ></idea-detail>

      </template>
    </div>
  `,

  data() {
    return {
      loading: true,
      error: null,
      ideas: [],
      themes: {},
      demoCoverage: [],
      selectedIdeaId: null,
      sortKey: 'bridging',
      sortDesc: true,
      scoringMethod: 'jsd',
      polarizationPenalty: true,
      currentPage: 1,
      pageSize: 25,
      tooltip: { visible: false, text: '', x: 0, y: 0 },
      _themesChart: null,
    };
  },

  computed: {
    sortedIdeas() {
      const arr = [...this.ideas];
      const key = this.sortKey;
      const desc = this.sortDesc;

      arr.sort((a, b) => {
        let va, vb;
        if (key === 'title') {
          va = (a.title || '').toLowerCase();
          vb = (b.title || '').toLowerCase();
          return desc ? vb.localeCompare(va) : va.localeCompare(vb);
        }
        if (key === 'likes') {
          va = a.reactions.upvotes;
          vb = b.reactions.upvotes;
        } else if (key === 'dislikes') {
          va = a.reactions.downvotes;
          vb = b.reactions.downvotes;
        } else if (key === 'approval') {
          const totalA = a.reactions.upvotes + a.reactions.downvotes;
          const totalB = b.reactions.upvotes + b.reactions.downvotes;
          va = totalA > 0 ? a.reactions.upvotes / totalA : -1;
          vb = totalB > 0 ? b.reactions.upvotes / totalB : -1;
        } else {
          // bridging — use selected scoring method
          const scoreKey = this.scoringMethod === 'wmga'
            ? (this.polarizationPenalty ? 'wmga_score' : 'wmga_score_no_penalty')
            : (this.polarizationPenalty ? 'consensus_score' : 'consensus_score_no_penalty');
          va = (a.bridging && a.bridging[scoreKey] != null) ? a.bridging[scoreKey] : -1;
          vb = (b.bridging && b.bridging[scoreKey] != null) ? b.bridging[scoreKey] : -1;
        }
        return desc ? vb - va : va - vb;
      });
      return arr;
    },
    totalPages() {
      return Math.max(1, Math.ceil(this.sortedIdeas.length / this.pageSize));
    },
    paginatedIdeas() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.sortedIdeas.slice(start, start + this.pageSize);
    },
  },

  watch: {
    sortKey() { this.currentPage = 1; },
    sortDesc() { this.currentPage = 1; },
  },

  async mounted() {
    try {
      // Use preloaded data from the app loading screen
      this.ideas = this.preloaded.ideas || [];
      this.themes = this.preloaded.themes || {};
      this.demoCoverage = (this.preloaded.demoCoverage && this.preloaded.demoCoverage.coverage) || [];
      this.loading = false;

      this.$nextTick(() => {
        this.renderThemesChart();
      });
    } catch (e) {
      console.error('[IdeasTab] mounted error:', e);
      this.error = 'Failed to load ideas data.';
      this.loading = false;
    }
  },

  beforeUnmount() {
    if (this._themesChart) this._themesChart.destroy();
  },

  methods: {
    sortBy(key) {
      if (this.sortKey === key) {
        this.sortDesc = !this.sortDesc;
      } else {
        this.sortKey = key;
        this.sortDesc = true;
      }
    },

    sortArrow(key) {
      if (this.sortKey !== key) return '';
      return this.sortDesc ? '▼' : '▲';
    },

    selectIdea(id) {
      this.selectedIdeaId = id;
    },

    showTooltip(event, text) {
      const rect = event.currentTarget.getBoundingClientRect();
      this.tooltip = {
        visible: true,
        text,
        x: rect.left + rect.width / 2,
        y: rect.bottom + 10,
      };
    },

    hideTooltip() {
      this.tooltip.visible = false;
    },

    approvalLabel(idea) {
      const total = idea.reactions.upvotes + idea.reactions.downvotes;
      if (total === 0) return 'N/A';
      return ((idea.reactions.upvotes / total) * 100).toFixed(0) + '%';
    },

    coverageClass(pct) {
      if (pct >= 70) return 'bridging-badge bridging-high';
      if (pct >= 40) return 'bridging-badge bridging-med';
      return 'bridging-badge bridging-low';
    },

    approvalClass(idea) {
      const total = idea.reactions.upvotes + idea.reactions.downvotes;
      if (total === 0) return 'bridging-badge bridging-na';
      const pct = (idea.reactions.upvotes / total) * 100;
      if (pct >= 70) return 'bridging-badge bridging-high';
      if (pct >= 45) return 'bridging-badge bridging-med';
      return 'bridging-badge bridging-low';
    },

    bridgingLabel(idea) {
      if (!idea.bridging) return 'N/A';
      const scoreKey = this.scoringMethod === 'wmga'
        ? (this.polarizationPenalty ? 'wmga_score' : 'wmga_score_no_penalty')
        : (this.polarizationPenalty ? 'consensus_score' : 'consensus_score_no_penalty');
      const score = idea.bridging[scoreKey];
      if (score == null) return 'N/A';
      return score.toFixed(1);
    },

    bridgingClass(idea) {
      if (!idea.bridging) return 'bridging-badge bridging-na';
      const scoreKey = this.scoringMethod === 'wmga'
        ? (this.polarizationPenalty ? 'wmga_score' : 'wmga_score_no_penalty')
        : (this.polarizationPenalty ? 'consensus_score' : 'consensus_score_no_penalty');
      const s = idea.bridging[scoreKey];
      if (s == null) return 'bridging-badge bridging-na';
      if (s >= 70) return 'bridging-badge bridging-high';
      if (s >= 45) return 'bridging-badge bridging-med';
      return 'bridging-badge bridging-low';
    },

    exportCSV() {
      const top = this.sortedIdeas.slice(0, 100);
      const demoDims = ['age_bucket', 'race', 'political_lean', 'region', 'urban_rural'];
      const demoHeaders = [];
      demoDims.forEach(dim => {
        demoHeaders.push(dim + '_upvotes');
        demoHeaders.push(dim + '_downvotes');
      });

      const headers = ['Rank', 'Title', 'Description', 'Consensus Score (JSD)', 'Consensus Score (WMGA)', 'Approval Rating', 'Likes', 'Dislikes', ...demoHeaders];

      const escapeCSV = (val) => {
        const s = String(val == null ? '' : val);
        if (s.includes(',') || s.includes('"') || s.includes('\n')) {
          return '"' + s.replace(/"/g, '""') + '"';
        }
        return s;
      };

      const formatBuckets = (buckets) => {
        if (!buckets || !Object.keys(buckets).length) return '';
        return Object.entries(buckets).map(([k, v]) => k + ': ' + v).join('; ');
      };

      const rows = top.map((idea, idx) => {
        const total = idea.reactions.upvotes + idea.reactions.downvotes;
        const approval = total > 0 ? ((idea.reactions.upvotes / total) * 100).toFixed(1) + '%' : 'N/A';
        const jsdScore = (idea.bridging && idea.bridging.consensus_score != null)
          ? idea.bridging.consensus_score.toFixed(1) : 'N/A';
        const wmgaScore = (idea.bridging && idea.bridging.wmga_score != null)
          ? idea.bridging.wmga_score.toFixed(1) : 'N/A';
        const bd = (idea.reactions && idea.reactions.demographic_breakdown) || {};

        const demoCols = [];
        demoDims.forEach(dim => {
          const dimData = bd[dim] || {};
          demoCols.push(formatBuckets(dimData.upvotes));
          demoCols.push(formatBuckets(dimData.downvotes));
        });

        return [
          idx + 1,
          idea.title || '',
          idea.body || '',
          jsdScore,
          wmgaScore,
          approval,
          idea.reactions.upvotes,
          idea.reactions.downvotes,
          ...demoCols,
        ].map(escapeCSV).join(',');
      });

      const csv = [headers.join(','), ...rows].join('\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'ideas_export.csv';
      a.click();
      URL.revokeObjectURL(url);
    },

    renderThemesChart() {
      const canvas = this.$refs.themesChart;
      if (!canvas || !this.themes.selections || !this.themes.selections.length) return;
      if (this._themesChart) { this._themesChart.destroy(); this._themesChart = null; }

      const sorted = [...this.themes.selections].sort((a, b) => b.count - a.count);
      this._themesChart = this._makeBarChart(
        canvas,
        sorted.map(s => s.idea),
        sorted.map(s => s.count),
        'selections',
      );
    },

    _makeBarChart(canvas, labels, data, tooltipSuffix) {
      const colors = [
        '#003366', '#0564B8', '#059669', '#d97706', '#dc2626',
        '#36A0E0', '#C2DFED', '#E4E0D4', '#84cc16', '#f97316',
      ];
      return new Chart(canvas, {
        type: 'bar',
        data: {
          labels,
          datasets: [{
            label: tooltipSuffix,
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
                label: (ctx) => `${ctx.raw} ${tooltipSuffix}`,
              },
            },
          },
        },
      });
    },
  },
};
