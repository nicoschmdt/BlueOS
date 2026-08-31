import {
  Action,
  getModule, Module, Mutation, VuexModule,
} from 'vuex-module-decorators'

import Notifier from '@/libs/notifier'
import { OneMoreTime } from '@/one-more-time'
import store from '@/store'
import { helper_service } from '@/types/frontend_services'
import { InternetConnectionState, Service } from '@/types/helper'
import back_axios, { isBackendOffline } from '@/utils/api'
import { connectivityVerdict, SiteStatuses } from '@/utils/connectivity'

const notifier = new Notifier(helper_service)

@Module({
  dynamic: true,
  store,
  name: 'helper',
})

class PingStore extends VuexModule {
  API_URL = '/helper/latest'

  has_internet: InternetConnectionState = InternetConnectionState.UNKNOWN

  services: Service[] = []

  reachable_hosts: string[] = []

  dns_failing = false

  internet_check_failures = 0

  checkInternetAccessTask = new OneMoreTime(
    { delay: 20000 },
  )

  updateWebServicesTask = new OneMoreTime(
    { delay: 10000 }, // scan_ports can take several seconds; a 5s delay stacked overlapping polls
  )

  @Mutation
  setHasInternet(has_internet: InternetConnectionState): void {
    this.has_internet = has_internet
  }

  @Mutation
  setReachableHosts(hosts: string[]): void {
    this.reachable_hosts = hosts
  }

  @Mutation
  setDnsFailing(dns_failing: boolean): void {
    this.dns_failing = dns_failing
  }

  @Mutation
  updateFoundServices(services: Service[]): void {
    this.services = services
  }

  @Mutation
  resetInternetCheckFailures(): void {
    this.internet_check_failures = 0
  }

  @Mutation
  incrementInternetCheckFailures(): void {
    this.internet_check_failures += 1
  }

  @Action
  async checkInternetAccess(): Promise<void> {
    back_axios({
      method: 'get',
      url: `${this.API_URL}/check_internet_access`,
      timeout: 10000,
    })
      .then((response) => {
        this.resetInternetCheckFailures()
        try {
          const sites = Object.values(response.data as SiteStatuses)
          this.setReachableHosts(sites.filter((item) => item.online).map((item) => item.site.hostname))

          const { state, dns_failing } = connectivityVerdict(sites)
          if (dns_failing !== undefined) {
            this.setDnsFailing(dns_failing)
          }
          if (state !== undefined) {
            this.setHasInternet(state)
          }
        } catch {
          // Helper answered; keep the last verdict if the body is unusable.
        }
      })
      .catch((error) => {
        // One timed-out poll is not a connectivity verdict.
        this.incrementInternetCheckFailures()
        if (this.internet_check_failures < 3) {
          return
        }
        this.setHasInternet(InternetConnectionState.UNKNOWN)
        this.setDnsFailing(false)
        this.setReachableHosts([])
        notifier.pushBackError('INTERNET_CHECK_FAIL', error)
      })
  }

  @Action
  async checkWebServices(): Promise<Service[]> {
    return back_axios({
      method: 'get',
      url: `${this.API_URL}/web_services`,
      timeout: 10000,
    })
      .then((response) => response.data as Service[])
      .catch((error) => {
        if (isBackendOffline(error)) { throw new Error(error) }
        const message = `Error scanning for services: ${error}`
        notifier.pushError('SERVICE_SCAN_FAIL', message)
        throw new Error(error)
      })
  }

  @Action
  async updateWebServices(): Promise<void> {
    this.checkWebServices()
      .then((services: Service[]) => {
        this.updateFoundServices(services.sort(
          (first: Service, second: Service) => first.port - second.port,
        ))
      })
      .catch(() => {
        this.updateFoundServices([])
      })
  }

  @Action
  async ping(options: {host: string, iface?: string}): Promise<boolean | undefined> {
    return back_axios({
      method: 'get',
      url: `${this.API_URL}/ping`,
      params: { host: options.host, interface_addr: options.iface },
      timeout: 15000,
    })
      .then((response) => response.data as boolean)
      .catch(() => undefined)
  }
}

export { PingStore }

const ping: PingStore = getModule(PingStore)

ping.checkInternetAccessTask.setAction(ping.checkInternetAccess)
ping.updateWebServicesTask.setAction(ping.updateWebServices)

export default ping
