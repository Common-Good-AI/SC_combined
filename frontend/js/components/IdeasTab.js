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
        <h3 class="section-title">Ideas ({{ sortedIdeas.length }})</h3>
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
                  Bridging Score
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
      selectedIdeaId: null,
      sortKey: 'bridging',
      sortDesc: true,
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
          // bridging
          va = (a.bridging && a.bridging.bridging_score != null) ? a.bridging.bridging_score : -1;
          vb = (b.bridging && b.bridging.bridging_score != null) ? b.bridging.bridging_score : -1;
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
      this.loading = false;

      this.$nextTick(() => this.renderThemesChart());
    } catch (e) {
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

    approvalClass(idea) {
      const total = idea.reactions.upvotes + idea.reactions.downvotes;
      if (total === 0) return 'bridging-badge bridging-na';
      const pct = (idea.reactions.upvotes / total) * 100;
      if (pct >= 70) return 'bridging-badge bridging-high';
      if (pct >= 45) return 'bridging-badge bridging-med';
      return 'bridging-badge bridging-low';
    },

    bridgingLabel(idea) {
      if (!idea.bridging || idea.bridging.bridging_score == null) return 'N/A';
      return idea.bridging.bridging_score.toFixed(1);
    },

    bridgingClass(idea) {
      if (!idea.bridging || idea.bridging.bridging_score == null) return 'bridging-badge bridging-na';
      const s = idea.bridging.bridging_score;
      if (s >= 70) return 'bridging-badge bridging-high';
      if (s >= 45) return 'bridging-badge bridging-med';
      return 'bridging-badge bridging-low';
    },

    renderThemesChart() {
      const canvas = this.$refs.themesChart;
      if (!canvas || !this.themes.selections || !this.themes.selections.length) return;

      const sorted = [...this.themes.selections].sort((a, b) => b.count - a.count);
      const labels = sorted.map(s => s.idea);
      const data = sorted.map(s => s.count);

      // Color palette
      const colors = [
        '#003366', '#0564B8', '#059669', '#d97706', '#dc2626',
        '#36A0E0', '#C2DFED', '#E4E0D4', '#84cc16', '#f97316',
      ];
      const bgColors = data.map((_, i) => colors[i % colors.length]);

      this._themesChart = new Chart(canvas, {
        type: 'bar',
        data: {
          labels,
          datasets: [{
            label: 'Selections',
            data,
            backgroundColor: bgColors,
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
                label: (ctx) => `${ctx.raw} selections`,
              },
            },
          },
        },
      });
    },
  },
};
