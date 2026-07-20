<template>
  <section class="agy-manager">
    <div class="agy-manager__header">
      <div>
        <div class="text-h5 font-weight-medium">{{ tm('agyCli.title') }}</div>
        <div class="text-body-2 text-medium-emphasis mt-1">
          {{ tm('agyCli.subtitle') }}
        </div>
      </div>
      <v-btn
        icon="mdi-refresh"
        variant="text"
        :loading="loadingStatus"
        :aria-label="tm('agyCli.refresh')"
        @click="loadStatus"
      ></v-btn>
    </div>

    <v-alert v-if="status?.latest_error" type="warning" variant="tonal" density="compact" class="mt-4">
      {{ tm('agyCli.latestCheckFailed') }}: {{ status.latest_error }}
    </v-alert>

    <div class="agy-manager__status mt-4">
      <v-chip :color="status?.installed ? 'success' : 'warning'" variant="tonal">
        <v-icon start>{{ status?.installed ? 'mdi-check-circle' : 'mdi-alert-circle' }}</v-icon>
        {{ status?.installed ? tm('agyCli.installed') : tm('agyCli.notInstalled') }}
      </v-chip>
      <v-chip v-if="status?.version" variant="outlined">v{{ status.version }}</v-chip>
      <v-chip v-if="status?.latest_version" variant="outlined">
        {{ tm('agyCli.latest') }} v{{ status.latest_version }}
      </v-chip>
      <v-chip v-if="status?.profile_initialized" color="info" variant="tonal">
        {{ tm('agyCli.profileInitialized') }}
      </v-chip>
    </div>

    <div v-if="status" class="agy-manager__details text-body-2 mt-4">
      <div><strong>{{ tm('agyCli.platform') }}:</strong> {{ status.platform }}</div>
      <div><strong>{{ tm('agyCli.executable') }}:</strong> {{ status.executable || status.managed_binary }}</div>
      <div><strong>{{ tm('agyCli.dataDirectory') }}:</strong> {{ status.data_directory }}</div>
    </div>

    <div class="agy-manager__actions mt-4">
      <v-btn
        color="primary"
        variant="tonal"
        prepend-icon="mdi-download"
        :loading="installing"
        @click="installOrUpdate"
      >
        {{ status?.installed ? tm('agyCli.update') : tm('agyCli.install') }}
      </v-btn>
      <v-btn
        color="info"
        variant="tonal"
        prepend-icon="mdi-login"
        :disabled="!status?.installed"
        @click="showAuth = true"
      >
        {{ tm('agyCli.webAuth') }}
      </v-btn>
    </div>

    <v-alert type="info" variant="tonal" density="compact" class="mt-4">
      {{ tm('agyCli.persistenceHint') }}
    </v-alert>

    <v-dialog v-model="showAuth" width="1100" persistent>
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6 d-flex align-center justify-space-between">
          <span>{{ tm('agyCli.authTitle') }}</span>
          <v-btn icon="mdi-close" variant="text" @click="closeAuth"></v-btn>
        </v-card-title>
        <v-divider></v-divider>
        <v-card-text class="pa-0">
          <v-alert type="warning" variant="tonal" density="compact" class="ma-4 mb-0">
            {{ tm('agyCli.authHint') }}
          </v-alert>
          <div ref="terminalHost" class="agy-terminal"></div>
        </v-card-text>
      </v-card>
    </v-dialog>
  </section>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { Terminal } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'

import { providerApi, type AgyCliStatusData } from '@/api/v1'
import { useModuleI18n } from '@/i18n/composables'

const props = defineProps<{
  source: Record<string, any>
  showMessage: (message: string, color?: string) => void
}>()

const { tm } = useModuleI18n('features/provider')
const status = ref<AgyCliStatusData | null>(null)
const loadingStatus = ref(false)
const installing = ref(false)
const showAuth = ref(false)
const terminalHost = ref<HTMLElement | null>(null)

let terminal: Terminal | null = null
let fitAddon: FitAddon | null = null
let socket: WebSocket | null = null
let resizeObserver: ResizeObserver | null = null

function proxyValue() {
  return String(props.source?.proxy || '').trim()
}

async function loadStatus() {
  loadingStatus.value = true
  try {
    const response = await providerApi.agyCliStatus(proxyValue())
    status.value = response.data.data
  } catch (error: any) {
    props.showMessage(error.response?.data?.message || error.message || tm('agyCli.statusFailed'), 'error')
  } finally {
    loadingStatus.value = false
  }
}

async function installOrUpdate() {
  installing.value = true
  try {
    const response = await providerApi.agyCliInstall(proxyValue())
    props.showMessage(response.data.message || tm('agyCli.installSuccess'))
    await loadStatus()
  } catch (error: any) {
    props.showMessage(error.response?.data?.message || error.message || tm('agyCli.installFailed'), 'error')
  } finally {
    installing.value = false
  }
}

function sendTerminalSize() {
  if (!terminal || socket?.readyState !== WebSocket.OPEN) return
  socket.send(JSON.stringify({ type: 'resize', cols: terminal.cols, rows: terminal.rows }))
}

function disposeTerminal() {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (socket && socket.readyState < WebSocket.CLOSING) {
    socket.close()
  }
  socket = null
  terminal?.dispose()
  terminal = null
  fitAddon = null
}

function closeAuth() {
  showAuth.value = false
}

async function openAuthTerminal() {
  await nextTick()
  if (!terminalHost.value) return

  disposeTerminal()
  terminal = new Terminal({
    cursorBlink: true,
    convertEol: true,
    fontFamily: 'Consolas, "Liberation Mono", monospace',
    fontSize: 14,
    scrollback: 5000,
    theme: { background: '#10131a', foreground: '#e7eaf0' },
  })
  fitAddon = new FitAddon()
  terminal.loadAddon(fitAddon)
  terminal.loadAddon(new WebLinksAddon((_event, uri) => {
    window.open(uri, '_blank', 'noopener,noreferrer')
  }))
  terminal.open(terminalHost.value)
  fitAddon.fit()
  terminal.focus()
  terminal.writeln(tm('agyCli.connecting'))

  const token = localStorage.getItem('token') || ''
  socket = new WebSocket(providerApi.agyCliAuthWebSocketUrl(token))
  terminal.onData((data) => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'input', data }))
    }
  })
  socket.onopen = () => {
    socket?.send(JSON.stringify({
      type: 'init',
      proxy: proxyValue(),
      cols: terminal?.cols || 120,
      rows: terminal?.rows || 32,
    }))
  }
  socket.onmessage = (event) => {
    let payload: any
    try {
      payload = JSON.parse(event.data)
    } catch {
      return
    }
    if (payload.type === 'output') {
      terminal?.write(String(payload.data || ''))
    } else if (payload.type === 'error') {
      terminal?.writeln(`\r\n${tm('agyCli.terminalError')}: ${payload.message}`)
    } else if (payload.type === 'exit') {
      terminal?.writeln(`\r\n${tm('agyCli.terminalExited')} (${payload.code ?? 0})`)
      loadStatus()
    }
  }
  socket.onclose = () => {
    terminal?.writeln(`\r\n${tm('agyCli.disconnected')}`)
  }

  resizeObserver = new ResizeObserver(() => {
    fitAddon?.fit()
    sendTerminalSize()
  })
  resizeObserver.observe(terminalHost.value)
}

watch(showAuth, (open) => {
  if (open) {
    openAuthTerminal()
  } else {
    disposeTerminal()
    loadStatus()
  }
})

watch(() => props.source?.id, () => loadStatus(), { immediate: true })
onBeforeUnmount(disposeTerminal)
</script>

<style scoped>
.agy-manager {
  padding: 24px;
}

.agy-manager__header,
.agy-manager__status,
.agy-manager__actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.agy-manager__header {
  justify-content: space-between;
}

.agy-manager__details {
  display: grid;
  gap: 6px;
  overflow-wrap: anywhere;
}

.agy-terminal {
  height: min(68vh, 680px);
  padding: 12px;
  background: #10131a;
}
</style>
