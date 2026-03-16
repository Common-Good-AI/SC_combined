const IdeaDetail = {
  props: {
    ideaId: { type: String, required: true },
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
                Bridging Score
                <span class="info-icon"
                      @mouseenter="showTooltip($event, 'Measures cross-demographic appeal (0-100). Factors in approval ratio (likes vs dislikes), engagement volume (more reactions = higher weight), demographic diversity (Political Lean 50%, Urban/Rural 20%, Age 10%, Race 10%, Region 10%), and engagement level. Higher = wider cross-group appeal with strong approval and participation.')"
                      @mouseleave="hideTooltip">
                  &#9432;
                </span>
              </div>
            </div>
          </div>

          <!-- Bridging dimension breakdown -->
          <div v-if="idea.bridging && idea.bridging.bridging_score != null" class="demo-section">
            <h4>Bridging Score Breakdown</h4>
            <div class="stat-grid">
              <div class="stat-card" v-if="idea.bridging.approval_factor != null">
                <div class="stat-value" style="font-size:1.1rem">{{ idea.bridging.approval_factor.toFixed(3) }}</div>
                <div class="stat-label">
                  Approval Factor
                  <span class="info-icon"
                        @mouseenter="showTooltip($event, 'Ratio of likes to total reactions (0–1). Higher = stronger overall approval.')"
                        @mouseleave="hideTooltip">&#9432;</span>
                </div>
              </div>
              <div class="stat-card" v-if="idea.bridging.engagement_factor != null">
                <div class="stat-value" style="font-size:1.1rem">{{ idea.bridging.engagement_factor.toFixed(3) }}</div>
                <div class="stat-label">
                  Engagement Factor
                  <span class="info-icon"
                        @mouseenter="showTooltip($event, 'Measures reaction volume relative to the most-reacted idea (0–1). An idea with the highest total reactions scores 1.000.')"
                        @mouseleave="hideTooltip">&#9432;</span>
                </div>
              </div>
              <div class="stat-card" v-for="(val, dim) in idea.bridging.per_dimension_scores" :key="dim">
                <div class="stat-value" style="font-size:1.1rem">{{ fmtScore(val) }}</div>
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
                <div class="demo-bar-row" v-for="cat in sortedCategories(data)" :key="cat">
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

          <!-- Author demographics -->
          <div v-if="idea.author_demographics" class="demo-section">
            <h4>Author Demographics</h4>
            <table style="font-size:0.85rem">
              <tr v-for="(val, key) in idea.author_demographics" :key="key">
                <td style="padding:0.3rem 0.75rem; color:#64748b; font-weight:500">{{ formatDim(key) }}</td>
                <td style="padding:0.3rem 0.75rem">{{ val || '—' }}</td>
              </tr>
            </table>
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
    bridgingLabel() {
      if (!this.idea || !this.idea.bridging || this.idea.bridging.bridging_score == null) return 'N/A';
      return this.idea.bridging.bridging_score.toFixed(1);
    },
    bridgingClass() {
      if (!this.idea || !this.idea.bridging || this.idea.bridging.bridging_score == null) return 'bridging-badge bridging-na';
      const s = this.idea.bridging.bridging_score;
      if (s >= 75) return 'bridging-badge bridging-high';
      if (s >= 50) return 'bridging-badge bridging-med';
      return 'bridging-badge bridging-low';
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
    sortedCategories(data) {
      const cats = new Set([
        ...Object.keys(data.upvotes || {}),
        ...Object.keys(data.downvotes || {}),
      ]);
      return [...cats].sort((a, b) => {
        const totalA = (data.upvotes[a] || 0) + (data.downvotes[a] || 0);
        const totalB = (data.upvotes[b] || 0) + (data.downvotes[b] || 0);
        return totalB - totalA;
      });
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
