import { InternetConnectionState } from '@/types/helper'
import { isIpAddress } from '@/utils/pattern_validators'

export interface CheckedSite {
  hostname: string
  path: string
  port: number
}

export interface SiteStatus {
  site: CheckedSite
  online: boolean
  error: string | null
  dns_failure: boolean
}

export type SiteStatuses = Record<string, SiteStatus>

export interface ConnectivityVerdict {
  /** `undefined` when this poll has no signal, meaning the previous value still stands. */
  state?: InternetConnectionState
  dns_failing?: boolean
}

// Helper aborts its probes on a deadline shorter than the resolver's own timeout, so a nameserver
// that swallows queries is cut off before it can report a resolution error.
const NO_ANSWER = 'timeout'

function failedToResolve(site: SiteStatus): boolean {
  return site.dns_failure || site.error === NO_ANSWER
}

export function connectivityVerdict(sites: SiteStatus[]): ConnectivityVerdict {
  const by_ip = sites.filter((item) => isIpAddress(item.site.hostname))
  const by_name = sites.filter((item) => !isIpAddress(item.site.hostname))

  // A site that did not answer inside Helper's budget carries no verdict.
  const answered = sites.filter((item) => item.error !== NO_ANSWER)
  if (answered.length === 0) {
    return {}
  }

  // IP answers but every hostname failed to resolve (or died as Helper's deadline).
  // Connection-refused / HTTP / TLS means the name resolved; that is LIMITED, not DNS.
  const dns_failing = by_name.length > 0
    && by_ip.some((item) => item.online)
    && by_name.every((item) => !item.online && failedToResolve(item))

  const online = sites.filter((item) => item.online)
  if (online.length === answered.length && !dns_failing) {
    return { state: InternetConnectionState.ONLINE, dns_failing }
  }
  return {
    state: online.length > 0 ? InternetConnectionState.LIMITED : InternetConnectionState.OFFLINE,
    dns_failing,
  }
}
