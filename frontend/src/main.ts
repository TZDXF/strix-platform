import { createApp } from 'vue'
import App from './App.vue'
import { initTheme } from './theme'
import './index.css'
// markstream-vue 样式需在 Tailwind reset 之后引入
import 'markstream-vue/index.css'

initTheme()

createApp(App).mount('#app')
