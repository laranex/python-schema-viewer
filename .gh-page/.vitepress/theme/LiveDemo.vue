<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useData, withBase } from 'vitepress'

// The viewer picks its theme from this localStorage key (laranex-schema-viewer-ui's theme.ts), which the
// same-origin demo shares with the site, and reads it only on load: write it, then (re)load the demo.
const THEME_KEY = 'laranex-schema-viewer-theme'

const { isDark } = useData()
const frame = ref(null)
const ready = ref(false)
const active = ref(false)
const src = computed(() => withBase('/demo/index.html'))
function storeTheme() {
  try { localStorage.setItem(THEME_KEY, isDark.value ? 'dark' : 'light') } catch { /* storage may be unavailable */ }
}
function loaded() { if (active.value) ready.value = true }
watch(isDark, () => {
  storeTheme()
  if (ready.value) frame.value?.contentWindow?.location.reload()
})
// The demo loads only on the client, after the theme is stored, so a prerendered page never shows it in the other theme.
onMounted(() => { storeTheme(); active.value = true })
</script>

<template>
  <section class="lx-demo" aria-label="Synthetic schema demonstration">
    <div class="lx-demo-bar"><span><i></i><i></i><i></i><b>INTERACTIVE DEMO</b></span><span>Synthetic workspace · no backend</span></div>
    <iframe ref="frame" :src="active ? src : undefined" title="Interactive ERD of a synthetic project workspace" loading="lazy" @load="loaded"></iframe>
    <div class="lx-demo-footer"><span>{{ ready ? 'Drag to explore · scroll to zoom · select for details' : 'Loading the demo when it comes into view…' }}</span><a :href="src" target="_blank" rel="noopener">Open full screen ↗</a></div>
  </section>
</template>
