<template>
  <div class="d-flex justify-center align-center">
    <v-card elevation="0">
      <v-stepper vertical elevation="0">
        <v-stepper-step
          step="1"
          :color="icon_color"
          :complete-icon="icon"
          :complete="true"
          active
          class="step-label"
        >
          {{ text }}
        </v-stepper-step>
      </v-stepper>
      <WifiManager
        v-if="!is_online && !checking"
        :show-top-bar="false"
        @current-network="(net) => connected = net != null"
      />
    </v-card>
  </div>
</template>

<script lang="ts">
import Vue from 'vue'

import WifiManager from '@/components/wifi/WifiManager.vue'
import { InternetConnectionState } from '@/types/helper'
import back_axios, { isBackendOffline } from '@/utils/api'
import { connectivityVerdict, SiteStatuses } from '@/utils/connectivity'

// The step only moves on by itself, so a warning that flashes past is no warning at all
const DNS_WARNING_DWELL_MS = 5000

export default Vue.extend({
  name: 'RequireInternet',
  components: {
    WifiManager,
  },
  data() {
    return {
      connected: false,
      checking: true,
      re_checking: false,
      is_online: false,
      dns_failing: false,
      timeout: 0,
    }
  },
  computed: {
    icon_color() {
      if (this.checking || this.re_checking || this.dns_failing) {
        return 'warning'
      }
      return this.is_online ? 'success' : 'error'
    },
    icon() {
      if (this.checking || this.re_checking) {
        return 'mdi-loading mdi-spin'
      }
      if (this.dns_failing) {
        return 'mdi-web-cancel'
      }
      return this.is_online ? 'mdi-check' : 'mdi-close'
    },
    text() {
      if (this.checking) {
        return 'Checking Internet Connection...'
      }
      if (this.dns_failing) {
        return 'Internet reachable by IP, but no hostname resolves.'
          + ' Downloads will fail until the DNS nameservers are fixed.'
      }
      return this.is_online ? 'Internet Connection Established' : 'No Internet Connection, please connect to a network'
    },
  },
  watch: {
    connected() {
      if (this.connected) {
        this.checking = true
        this.checkInternet()
      }
    },
    is_online() {
      if (this.is_online) {
        this.$emit('online')
      }
    },
  },
  async mounted() {
    this.checkInternet()
  },
  methods: {
    checkInternet() {
      this.re_checking = true
      back_axios({
        method: 'get',
        url: '/helper/latest/check_internet_access',
        timeout: 10000,
      })
        .then((response) => {
          const sites = Object.values(response.data as SiteStatuses)
          const { state, dns_failing } = connectivityVerdict(sites)
          if (dns_failing !== undefined) {
            this.dns_failing = dns_failing
          }
          if (state !== undefined) {
            this.is_online = state !== InternetConnectionState.OFFLINE
          }
          this.checking = false
          this.re_checking = false
        })
        .catch((error) => {
          if (isBackendOffline(error)) { return }
          this.is_online = false
          this.dns_failing = false
        })
        .finally(() => {
          if (!this.is_online) {
            this.timeout = setTimeout(() => {
              this.checkInternet()
            }, 5000)
          } else {
            clearInterval(this.timeout)
            this.timeout = setTimeout(() => {
              this.$emit('next')
            }, this.dns_failing ? DNS_WARNING_DWELL_MS : 1000)
          }
        })
    },
  },
})
</script>
