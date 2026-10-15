import { createApp } from 'vue'
import { createPinia } from 'pinia'
import LoadBenchmark from './LoadBenchmark.vue'

createApp(LoadBenchmark).use(createPinia()).mount('#app')
