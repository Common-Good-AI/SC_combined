const { createApp } = Vue;

const app = createApp({
  data() {
    return {
      activeTab: 'participation',
    };
  },
});

// Register components
app.component('participation-tab', ParticipationTab);
app.component('ideas-tab', IdeasTab);
app.component('idea-detail', IdeaDetail);

app.mount('#app');
