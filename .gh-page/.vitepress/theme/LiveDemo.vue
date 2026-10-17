<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useData, withBase } from 'vitepress'

const { isDark } = useData()
const frame = ref(null)
const ready = ref(false)
const src = computed(() => withBase('/demo/index.html'))
function syncTheme() {
  const root = frame.value?.contentDocument?.documentElement
  if (root) root.dataset.theme = isDark.value ? 'dark' : 'light'
}
function loaded() { ready.value = true; syncTheme() }
watch(isDark, syncTheme)
onMounted(syncTheme)
</script>

<template>
  <section class="lx-demo" aria-label="Synthetic schema demonstration">
    <div class="lx-demo-bar"><span><i></i><i></i><i></i><b>INTERACTIVE DEMO</b></span><span>Synthetic workspace · no backend</span></div>
    <iframe ref="frame" :src="src" title="Interactive ERD of a synthetic project workspace" loading="lazy" @load="loaded"></iframe>
    <div class="lx-demo-footer"><span>{{ ready ? 'Drag to explore · scroll to zoom · select for details' : 'Loading the demo when it comes into view…' }}</span><a :href="src" target="_blank" rel="noopener">Open full screen ↗</a></div>
  </section>
</template>
