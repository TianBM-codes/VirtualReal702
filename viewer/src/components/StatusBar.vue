<template>
  <div id="status" :class="store.statusType">{{ store.statusMsg }}</div>
</template>

<script setup>
import { useViewerStore } from '../store/viewer'
const store = useViewerStore()
</script>
