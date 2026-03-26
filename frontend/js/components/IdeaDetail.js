const IdeaDetail = {
  props: {
    ideaId: { type: String, required: true },
    scoringMethod: { type: String, default: 'jsd' },
    polarizationPenalty: { type: Boolean, default: true },
  },
  emits: ['close'],
  template: `
    <div class="modal-overlay" @click.self="$emit('close')">
      <div class="modal">

        <!-- Fixed-position bridging tooltip -->
        <div v-if="tooltip.visible"
             class="fixed-tooltip"
             :style="{ top: tooltip.y + 'px', left: tooltip.x + 'px' }">
          {{ tooltip.text }}
        </div>
        <div v-if="loading" class="loading">Loading idea details…</div>
        <div v-else-if="error" class="error">{{ error }}</div>
        <template v-else>
          <div class="modal-header">
            <h2>{{ idea.title }}</h2>
            <button class="close-btn" @click="$emit('close')">&times;</button>
          </div>

          <p class="detail-body" v-if="idea.body">{{ idea.body }}</p>

          <!-- Stats row -->
          <div class="stat-grid">
            <div class="stat-card">
              <div class="stat-value" style="color:#22c55e">{{ idea.reactions.upvotes }}</div>
              <div class="stat-label">Likes</div>
            </div>
            <div class="stat-card">
              <div class="stat-value" style="color:#ef4444">{{ idea.reactions.downvotes }}</div>
              <div class="stat-label">Dislikes</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">{{ idea.reactions.total }}</div>
              <div class="stat-label">Total Reactions</div>
            </div>
            <div class="stat-card">
              <div class="stat-value">
                <span :class="bridgingClass">{{ bridgingLabel }}</span>
              </div>
              <div class="stat-label">
                Consensus Score
                <span class="info-icon"
                      @mouseenter="showTooltip($event, scoringMethod === 'wmga'
                        ? 'Weighted Mean Group Approval (0-100). Population-weighted average of each demographic group\\'s Bayesian-smoothed approval rate. Higher = broader cross-group support.'
                        : 'Measures cross-demographic consensus (0-100). Combines approval rate (likes vs total), demographic diversity of supporters (Political Lean 50%, Urban/Rural 20%, Age 10%, Race 10%, Region 10%), and a polarization penalty if groups disagree sharply. Higher = broader cross-group support.')"
                      @mouseleave="hideTooltip">
                  &#9432;
                </span>
              </div>
            </div>
          </div>

          <!-- Bridging dimension breakdown -->
          <div v-if="idea.bridging && activeScore != null" class="demo-section">
            <h4>Consensus Score Breakdown
              <span style="font-size:0.78rem; font-weight:400; color:#94a3b8; margin-left:6px;">
                ({{ scoringMethod === 'wmga' ? 'WMGA' : 'JSD' }})
              </span>
            </h4>
            <div class="stat-grid">
              <div class="stat-card" v-if="idea.bridging.approval_ratio != null">
                <div class="stat-value" style="font-size:1.1rem">{{ (idea.bridging.approval_ratio * 100).toFixed(1) }}%</div>
                <div class="stat-label">
                  Approval Rate
                  <span class="info-icon"
                        @mouseenter="showTooltip($event, 'Percentage of reactions that are likes.')"
                        @mouseleave="hideTooltip">&#9432;</span>
                </div>
              </div>
              <div class="stat-card" v-for="(val, dim) in activeDimensionScores" :key="dim">
                <div class="stat-value" style="font-size:1.1rem">{{ scoringMethod === 'wmga' ? (val * 100).toFixed(1) + '%' : fmtScore(val) }}</div>
                <div class="stat-label">{{ formatDim(dim) }}</div>
              </div>
            </div>
          </div>

          <!-- Demographic breakdown of reactions -->
          <template v-if="idea.reactions.demographic_breakdown">
            <div class="demo-section"
                 v-for="(data, dim) in idea.reactions.demographic_breakdown"
                 :key="dim">
              <h4>{{ formatDim(dim) }}</h4>
              <div class="demo-bars">
                <div class="demo-bar-row" v-for="cat in sortedCategories(data, dim)" :key="cat">
                  <span class="demo-bar-label">{{ cat }}</span>
                  <div class="demo-bar-track">
                    <div class="demo-bar-up"
                         :style="{ width: barPct(data.upvotes[cat], data.downvotes[cat], 'up') }"></div>
                    <div class="demo-bar-down"
                         :style="{ width: barPct(data.upvotes[cat], data.downvotes[cat], 'down') }"></div>
                  </div>
                  <span class="demo-bar-count">
                    {{ (data.upvotes[cat] || 0) + (data.downvotes[cat] || 0) }}
                  </span>
                </div>
              </div>
            </div>
          </template>

          <!-- Author Statistics -->
          <div v-if="idea.author_demographics || idea.created_at" class="demo-section">
            <h4>Author Statistics</h4>

            <!-- Submission date -->
            <div v-if="idea.created_at" class="author-meta">
              <span class="author-meta-label">Submitted</span>
              <span class="author-meta-value">{{ formattedDate }}</span>
            </div>

            <!-- Demographic chips -->
            <div v-if="idea.author_demographics" class="author-chips">
              <template v-for="(val, dim) in idea.author_demographics" :key="dim">
                <div v-if="val" class="author-chip">
                  <span class="author-chip-label">{{ formatDim(dim) }}</span>
                  <span class="author-chip-value">{{ val }}</span>
                </div>
              </template>
              <span v-if="!Object.values(idea.author_demographics).some(v => v)" class="author-no-demo">
                No demographic data available
              </span>
            </div>

            <!-- Group vs overall approval -->
            <div v-if="authorGroupStats.length" class="author-approval">
              <div class="author-approval-header">Author's Group Approval vs. Overall</div>
              <div class="author-approval-row" v-for="stat in authorGroupStats" :key="stat.dim">
                <div class="author-approval-dim">
                  <span class="author-approval-dim-name">{{ formatDim(stat.dim) }}:</span>
                  <span class="author-approval-group-name">{{ stat.group }}</span>
                </div>
                <div class="author-approval-bars">
                  <div class="author-approval-bar-row">
                    <span class="author-approval-bar-label">Group ({{ stat.groupTotal }})</span>
                    <div class="author-approval-bar-track">
                      <div class="author-approval-bar-fill"
                           :class="stat.groupPct >= stat.overallPct ? 'fill-positive' : 'fill-negative'"
                           :style="{ width: stat.groupPct + '%' }"></div>
                    </div>
                    <span class="author-approval-bar-pct">{{ stat.groupPct.toFixed(0) }}%</span>
                  </div>
                  <div class="author-approval-bar-row">
                    <span class="author-approval-bar-label">Overall ({{ stat.overallTotal }})</span>
                    <div class="author-approval-bar-track">
                      <div class="author-approval-bar-fill fill-overall"
                           :style="{ width: stat.overallPct + '%' }"></div>
                    </div>
                    <span class="author-approval-bar-pct">{{ stat.overallPct.toFixed(0) }}%</span>
                  </div>
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
      idea: null,
      tooltip: { visible: false, text: '', x: 0, y: 0 },
    };
  },

  computed: {
    activeScore() {
      if (!this.idea || !this.idea.bridging) return null;
      if (this.scoringMethod === 'wmga') {
        return this.polarizationPenalty ? this.idea.bridging.wmga_score : this.idea.bridging.wmga_score_no_penalty;
      }
      return this.polarizationPenalty ? this.idea.bridging.consensus_score : this.idea.bridging.consensus_score_no_penalty;
    },
    bridgingLabel() {
      const s = this.activeScore;
      if (s == null) return 'N/A';
      return s.toFixed(1);
    },
    bridgingClass() {
      const s = this.activeScore;
      if (s == null) return 'bridging-badge bridging-na';
      if (s >= 75) return 'bridging-badge bridging-high';
      if (s >= 50) return 'bridging-badge bridging-med';
      return 'bridging-badge bridging-low';
    },
    activeDimensionScores() {
      if (!this.idea || !this.idea.bridging) return {};
      if (this.scoringMethod === 'wmga') {
        return this.polarizationPenalty
          ? (this.idea.bridging.wmga_per_dimension || {})
          : (this.idea.bridging.wmga_per_dimension_no_penalty || {});
      }
      return this.idea.bridging.per_dimension_scores || {};
    },
    formattedDate() {
      if (!this.idea?.created_at) return null;
      return new Date(this.idea.created_at).toLocaleDateString('en-US', {
        year: 'numeric', month: 'long', day: 'numeric',
      });
    },
    authorGroupStats() {
      if (!this.idea?.author_demographics || !this.idea?.reactions?.demographic_breakdown) return [];
      const { upvotes, downvotes, total } = this.idea.reactions;
      const overallPct = total > 0 ? (upvotes / total) * 100 : 0;
      const results = [];
      for (const [dim, authorGroup] of Object.entries(this.idea.author_demographics)) {
        if (!authorGroup) continue;
        const dimData = this.idea.reactions.demographic_breakdown[dim];
        if (!dimData) continue;
        const groupUp = dimData.upvotes?.[authorGroup] || 0;
        const groupDown = dimData.downvotes?.[authorGroup] || 0;
        const groupTotal = groupUp + groupDown;
        if (groupTotal < 3) continue;
        results.push({
          dim,
          group: authorGroup,
          groupPct: (groupUp / groupTotal) * 100,
          groupTotal,
          overallPct,
          overallTotal: total,
        });
      }
      return results;
    },
  },

  async mounted() {
    try {
      const res = await fetch(`/api/ideas/${encodeURIComponent(this.ideaId)}`);
      if (!res.ok) throw new Error('Not found');
      this.idea = await res.json();
      this.loading = false;
    } catch (e) {
      this.error = 'Failed to load idea details.';
      this.loading = false;
    }
  },

  methods: {
    formatDim(dim) {
      return dim.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    },
    fmtScore(val) {
      if (val == null) return '—';
      if (typeof val !== 'number') return val;
      return val.toFixed(3);
    },
    sortedCategories(data, dim) {
      const cats = [...new Set([
        ...Object.keys(data.upvotes || {}),
        ...Object.keys(data.downvotes || {}),
      ])];

      if (dim === 'age_bucket') {
        const ORDER = ['Under 18', '18-29', '30-39', '40-49', '50-59', '60-69', '65+', '70+'];
        return cats.sort((a, b) => {
          const ai = ORDER.indexOf(a), bi = ORDER.indexOf(b);
          if (ai === -1 && bi === -1) return a.localeCompare(b);
          return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
        });
      }

      if (dim === 'political_lean') {
        const ORDER = ['Very Conservative', 'Conservative', 'Moderate', 'Liberal', 'Very Liberal', 'Not sure', 'Prefer not to say'];
        return cats.sort((a, b) => {
          const ai = ORDER.indexOf(a), bi = ORDER.indexOf(b);
          if (ai === -1 && bi === -1) return a.localeCompare(b);
          return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
        });
      }

      if (dim === 'race') {
        return cats.sort((a, b) => {
          const aP = a.toLowerCase().startsWith('prefer'), bP = b.toLowerCase().startsWith('prefer');
          if (aP !== bP) return aP ? 1 : -1;
          return a.localeCompare(b);
        });
      }

      // region, urban_rural, and any other dims: alphabetical
      return cats.sort((a, b) => a.localeCompare(b));
    },
    barPct(upCount, downCount, type) {
      const up = upCount || 0;
      const down = downCount || 0;
      const total = up + down;
      if (total === 0) return '0%';
      if (type === 'up') return ((up / total) * 100).toFixed(1) + '%';
      return ((down / total) * 100).toFixed(1) + '%';
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
  },
};
