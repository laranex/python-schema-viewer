<script setup>
import { ref } from 'vue'
import { withBase } from 'vitepress'

const command = "pip install 'laranex-schema-viewer[sqlmodel,mcp]'"
const message = ref('Copy command')
async function copy() {
  try {
    await navigator.clipboard.writeText(command)
    message.value = 'Copied'
  } catch {
    message.value = 'Select and copy the command below'
  }
}
</script>

<template>
  <header class="lx-package">
    <p class="lx-eyebrow"><span></span> PYTHON TOOLING, WITH A CLEARER PICTURE</p>
    <h1>Laranex<br /><span>Schema Viewer</span></h1>
    <p class="lx-lead">Understand how your data fits together.<br />Tables, constraints, and ORM relationships — in one explorable view.</p>
    <div class="lx-tags"><span>SQLAlchemy 2.x</span><span>SQLModel</span><span>Django</span><span>Tortoise</span><span>Peewee</span><span>Read-only MCP</span><span>MIT</span></div>
    <div class="lx-install"><span aria-hidden="true">$</span><code>{{ command }}</code><button @click="copy" :aria-label="message">{{ message === 'Copied' ? 'Copied ✓' : 'Copy' }}</button></div>
    <p class="lx-caption">Requires Python 3.10+. Choose your ORM extra: <code>sqlalchemy</code>, <code>sqlmodel</code>, <code>django</code>, <code>tortoise</code>, or <code>peewee</code>; add <code>mcp</code> for agents.</p>
    <span class="sr-only" role="status">{{ message }}</span>
    <div class="lx-actions"><a class="lx-button primary" :href="withBase('/quick-start.html')">Get started <span aria-hidden="true">→</span></a><a class="lx-button secondary" :href="withBase('/demo.html')">Explore the live demo</a></div>
  </header>
</template>
