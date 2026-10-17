import { defineConfig } from 'vitepress'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const base = '/python-schema-viewer/'
const repository = 'https://github.com/laranex/python-schema-viewer'
const require = createRequire(import.meta.url)

export default defineConfig({
  title: 'Laranex Schema Viewer',
  description: 'Explore SQLAlchemy, SQLModel, Django, Tortoise ORM, and Peewee definitions with an interactive ERD, portable JSON snapshots, and read-only MCP tools.',
  lang: 'en-US',
  base,
  cleanUrls: false,
  appearance: 'dark',
  srcDir: '../docs',
  srcExclude: ['README.md', 'architecture.md', 'validation.md', 'agent-skills-validation.md'],
  vite: {
    publicDir: fileURLToPath(new URL('../public', import.meta.url)),
    // Markdown lives outside the Node project; resolve Vue from this project.
    resolve: { alias: [
      { find: /^vue$/, replacement: require.resolve('vue/dist/vue.runtime.esm-bundler.js') },
      { find: 'vue/server-renderer', replacement: require.resolve('vue/server-renderer') },
    ] },
  },
  lastUpdated: false,
  sitemap: { hostname: 'https://laranex.github.io/python-schema-viewer/' },
  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: `${base}logo.svg` }],
    ['meta', { name: 'theme-color', content: '#09090b' }],
    ['meta', { property: 'og:type', content: 'website' }],
    ['meta', { property: 'og:site_name', content: 'Laranex Schema Viewer' }],
  ],
  markdown: { theme: { light: 'github-light', dark: 'github-dark' }, lineNumbers: false },
  themeConfig: {
    logo: '/logo.svg',
    siteTitle: 'Laranex',
    nav: [
      { text: 'Python Schema Viewer', link: '/introduction' },
      { text: 'Live demo', link: '/demo' },
      { text: 'Laranex projects', link: 'https://laranex.vercel.app/' },
    ],
    sidebar: [
      { text: 'GET STARTED', items: [
        { text: 'Introduction', link: '/introduction' },
        { text: 'Installation', link: '/installation' },
        { text: 'Quick start', link: '/quick-start' },
        { text: 'Supported ORMs', link: '/orm-support' },
      ] },
      { text: 'WORK WITH YOUR SCHEMA', items: [
        { text: 'SQLAlchemy', link: '/sqlalchemy' },
        { text: 'SQLModel', link: '/sqlmodel' },
        { text: 'Django', link: '/django' },
        { text: 'Tortoise ORM', link: '/tortoise' },
        { text: 'Peewee', link: '/peewee' },
        { text: 'Discovery & extraction', link: '/discovery' },
        { text: 'Interactive viewer', link: '/viewer' },
        { text: 'Live demo', link: '/demo' },
      ] },
      { text: 'REFERENCE', items: [
        { text: 'CLI & configuration', link: '/cli' },
        { text: 'MCP for agents', link: '/mcp' },
        { text: 'Database introspection', link: '/database' },
        { text: 'Build security', link: '/security' },
        { text: 'Agent skills', link: '/agent-skills' },
        { text: 'Troubleshooting', link: '/troubleshooting' },
      ] },
    ],
    search: { provider: 'local', options: { detailedView: true } },
    outline: { level: [2, 3], label: 'On this page' },
    socialLinks: [{ icon: 'github', link: repository }],
    editLink: { pattern: `${repository}/edit/main/docs/:path`, text: 'Improve this page on GitHub' },
    footer: { message: 'Open source under the MIT license.', copyright: 'Laranex · Built by developers, for developers.' },
    docFooter: { prev: 'Previous page', next: 'Next page' },
    externalLinkIcon: true,
  },
})
