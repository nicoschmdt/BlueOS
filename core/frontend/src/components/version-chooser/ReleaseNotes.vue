<template>
  <div>
    <v-progress-circular
      v-if="loading"
      indeterminate
      size="24"
      color="primary"
    />
    <div v-else-if="notes">
      <!-- Markdown compiled by us from notes with their raw html escaped, so it is safe -->
      <!-- eslint-disable -->
      <div
        class="release-notes"
        v-html="compiled_notes"
      />
      <!-- eslint-enable -->
      <a
        :href="notes.url"
        target="_blank"
        rel="noopener noreferrer"
      >
        Full release notes
      </a>
    </div>
    <p
      v-else
      class="text-caption text--secondary ma-0"
    >
      Release notes are not available for this version.
    </p>
  </div>
</template>

<script lang="ts">
import { marked } from 'marked'
import Vue from 'vue'

import { ReleaseNotes } from '@/types/version-chooser'
import { getReleaseNotes } from '@/utils/version_chooser'

export default Vue.extend({
  name: 'ReleaseNotes',
  props: {
    repository: {
      type: String,
      required: true,
    },
    tag: {
      type: String,
      required: true,
    },
  },
  data() {
    return {
      notes: undefined as (undefined | ReleaseNotes),
      loading: true,
    }
  },
  computed: {
    compiled_notes(): string {
      // Escaping '<' keeps any html in the notes from being rendered, as markdown allows it through
      return marked(this.notes?.body.replaceAll('<', '&lt;') ?? '')
    },
  },
  async mounted() {
    this.notes = await getReleaseNotes(this.repository, this.tag)
    this.loading = false
  },
})
</script>

<style>
div.release-notes h3 {
  font-size: 1.1rem;
  margin-top: 10px;
}

div.release-notes ul {
  margin-bottom: 10px;
}
</style>
