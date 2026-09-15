import { gt as sem_ver_greater, SemVer } from 'semver'

import Notifier from '@/libs/notifier'
import { version_chooser_service } from '@/types/frontend_services'
import {
  DockerLoginInfo,
  LocalVersionsQuery, ReleaseNotes, Version, VersionsQuery, VersionType,
} from '@/types/version-chooser'
import back_axios from '@/utils/api'
import { fetchWithVehicleFallback } from '@/utils/helper_functions'

const API_URL = '/version-chooser/v1.0'
const DEFAULT_REMOTE_IMAGE = 'bluerobotics/blueos-core'
const RELEASES_API_URL = 'https://api.github.com/repos/bluerobotics/BlueOS/releases?per_page=100'
const DOCS_URL = 'https://blueos.cloud/docs'

const notifier = new Notifier(version_chooser_service)

function fixVersion(version: string): string | null {
  /** It turned out that our semvers are wrong... oopss
  This turns 1.0.0.beta12 into 1.0.0-beta.12
  Additionally filters out tags with no '.' in it, which
  can be improved
  */
  if (version.includes('.beta')) {
    return version.replace('.beta', '-beta.')
  }
  if (!version.includes('.')) {
    return null
  }
  return version
}

function toSemVer(version: string): SemVer | undefined {
  const fixed_version = fixVersion(version)
  if (fixed_version == null) {
    return undefined
  }

  try {
    return new SemVer(fixed_version)
  } catch (error) {
    return undefined
  }
}

function isSemVer(version: string): boolean {
  // validates a version as SemVer compliant
  return toSemVer(version) !== undefined
}

function getVersionType(version: Version | null) : VersionType | undefined {
  const tag = version?.tag
  if (tag === undefined) {
    return undefined
  }

  if (tag === 'master') { return VersionType.Master }
  if (isSemVer(tag) && tag.includes('beta')) { return VersionType.Beta }
  if (isSemVer(tag) && !tag.includes('beta')) { return VersionType.Stable }
  return VersionType.Custom
}

function sortVersions(versions: Version[]): Version[] {
  return versions.sort(
    (a: Version, b: Version) => {
      const ver_a = fixVersion(a.tag)
      const ver_b = fixVersion(b.tag)
      if (ver_a === null) {
        return 1
      }
      if (ver_b === null) {
        return -1
      }
      return sem_ver_greater(new SemVer(ver_a), new SemVer(ver_b)) === true ? -1 : 1
    },
  )
}

function compareVersions(a: Version, b: Version): number {
  return Date.parse(b.last_modified) - Date.parse(a.last_modified)
}

function sortImages(versions_query: VersionsQuery): VersionsQuery {
  return {
    local: versions_query.local.sort(compareVersions),
    remote: versions_query.remote.sort(compareVersions),
    error: versions_query.error,
  }
}

function getLatestBeta(versions_query: VersionsQuery): Version | undefined {
  const ordered_list = sortVersions(
    versions_query.remote
      .filter((image) => isSemVer(image.tag) && image.tag.includes('beta')),
  )
  return ordered_list?.[0]
}

function getLatestStable(versions_query: VersionsQuery): Version | undefined {
  const ordered_list = sortVersions(
    versions_query.remote
      .filter((image) => isSemVer(image.tag) && !image.tag.includes('beta')),
  )
  return ordered_list?.[0]
}

function getMaster(versions_query: VersionsQuery): Version | undefined {
  return versions_query.remote.find((version: Version) => version.tag === 'master')
}

function getLatestVersion(versions_query: VersionsQuery, current_version: Version): Version | undefined {
  const beta_version = getLatestBeta(versions_query)
  const stable_version = getLatestStable(versions_query)

  switch (getVersionType(current_version)) {
    case VersionType.Master:
      return getMaster(versions_query)

    case VersionType.Beta: {
      let last_version = beta_version
      if (stable_version !== undefined && beta_version !== undefined) {
        [last_version] = sortVersions([stable_version, beta_version])
      }
      return versions_query.remote.find((version) => version.tag === last_version?.tag)
    }

    case VersionType.Stable: {
      return versions_query.remote.find((version) => version.tag === stable_version?.tag)
    }

    case VersionType.Custom:
    default:
      return undefined
  }
}

interface GitHubRelease {
  tag_name: string,
  body: string | null,
  html_url: string,
  draft: boolean,
}

let release_notes_request: Promise<Map<string, ReleaseNotes>> | undefined

async function requestReleaseNotes(): Promise<Map<string, ReleaseNotes>> {
  const response = await fetchWithVehicleFallback(RELEASES_API_URL)
  if (!response.ok) {
    throw new Error(`GitHub answered with ${response.status} while fetching the BlueOS releases`)
  }
  const releases = await response.json() as GitHubRelease[]
  return new Map(
    releases
      .filter((release) => !release.draft && release.body)
      .map((release): [string, ReleaseNotes] => [release.tag_name, {
        tag: release.tag_name,
        body: release.body ?? '',
        url: release.html_url,
      }]),
  )
}

/**
 * Fetches the notes published for every BlueOS release, indexed by version tag.
 * Memoized, so a page listing many versions performs a single request. A failed request is
 * dropped from the cache to let the next reader retry, and resolves to an empty index:
 * release notes are cosmetic and should never surface an error to the user.
 * @returns Release notes by version tag, empty when GitHub is unreachable
 */
async function fetchReleaseNotes(): Promise<Map<string, ReleaseNotes>> {
  release_notes_request = release_notes_request ?? requestReleaseNotes()
    .catch(() => {
      release_notes_request = undefined
      return new Map<string, ReleaseNotes>()
    })
  return release_notes_request
}

/**
 * Fetches the release notes for a single image, when it has any.
 * Only the official BlueOS image is covered: forks have their own releases, and tags that are
 * not versions (`master`, `factory`, branch builds) have no release to point at.
 * @param repository - Docker repository of the image, e.g. `bluerobotics/blueos-core`
 * @param tag - Docker tag of the image, e.g. `1.4.5` or `1.5.0-beta.42`
 * @returns Notes for that version, or undefined when there are none
 */
async function getReleaseNotes(repository: string, tag: string): Promise<ReleaseNotes | undefined> {
  const version = fixVersion(tag)
  if (repository !== DEFAULT_REMOTE_IMAGE || version === null || !isSemVer(tag)) {
    return undefined
  }
  return (await fetchReleaseNotes()).get(version)
}

/**
 * Documentation URL for a given version.
 * The docs site publishes one path per release line (`/docs/1.4/`) up to the current stable one.
 * Pre-releases of a line that is not out yet, and anything that is not a 1.x+ version, are only
 * covered by `/docs/latest/`.
 * @param tag - Docker tag of the image, e.g. `1.4.5` or `1.5.0-beta.42`
 * @param latest_stable_tag - Newest stable tag known, when the remote versions are available
 * @returns URL of the documentation that matches the version as closely as possible
 */
function getDocsUrl(tag: string, latest_stable_tag?: string): string {
  const version = toSemVer(tag)
  if (version === undefined || version.major < 1) {
    return `${DOCS_URL}/latest/`
  }

  const latest_stable = latest_stable_tag === undefined ? undefined : toSemVer(latest_stable_tag)
  const release_line = new SemVer(`${version.major}.${version.minor}.0`)
  const line_is_documented = latest_stable === undefined
    ? version.prerelease.length === 0
    : !sem_ver_greater(release_line, latest_stable)

  return line_is_documented ? `${DOCS_URL}/${version.major}.${version.minor}/` : `${DOCS_URL}/latest/`
}

async function loadLocalVersions(): Promise<LocalVersionsQuery> {
  return back_axios({
    method: 'get',
    url: `${API_URL}/version/available/local`,
  }).then((response) => {
    const available_versions = response.data as LocalVersionsQuery
    available_versions.local = available_versions.local.sort(compareVersions)
    return available_versions
  })
}

async function loadAvailableVersions(remote_image_name?: string): Promise<VersionsQuery> {
  remote_image_name = remote_image_name ?? DEFAULT_REMOTE_IMAGE
  return back_axios({
    method: 'get',
    url: `${API_URL}/version/available/${remote_image_name}`,
  }).then((response) => {
    const available_versions = response.data as VersionsQuery
    return sortImages(available_versions)
  })
}

async function loadCurrentVersion(): Promise<Version> {
  return back_axios({
    method: 'get',
    url: `${API_URL}/version/current/`,
  }).then((response) => response.data as Version)
}

async function loadBootstrapCurrentVersion(): Promise<string | undefined> {
  return back_axios({
    method: 'get',
    url: `${API_URL}/bootstrap/current/`,
    // eslint-disable-next-line no-extra-parens
  }).then((response) => (typeof response.data === 'object' ? undefined : response.data))
    // The update process may fail and the user may be in bootstrap-backup
    // This allows us to have the frontend working without crashing
    // But still visible on the back
    .catch((error) => {
      const message = `Failed to fetch bootstrap version: ${error}`
      notifier.pushWarning('VERSION_CHOOSER_FAILED_BOOTSTRAP_VERSION', message)
      return undefined
    })
}

async function dockerLogin(info: DockerLoginInfo): Promise<void> {
  await back_axios({
    method: 'post',
    url: `${API_URL}/docker/login/`,
    data: info,
  })
}

async function dockerLogout(info: DockerLoginInfo): Promise<void> {
  await back_axios({
    method: 'post',
    url: `${API_URL}/docker/logout/`,
    data: info,
  })
}

async function dockerAccounts(): Promise<DockerLoginInfo[]> {
  const data = await back_axios({
    method: 'get',
    url: `${API_URL}/docker/accounts/`,
  })

  return data.data as DockerLoginInfo[]
}

async function getFactoryVersion(): Promise<string> {
  const response = await back_axios({
    method: 'get',
    url: `${API_URL}/version/factory/`,
  })

  return response.data as string
}

export {
  DEFAULT_REMOTE_IMAGE,
  dockerAccounts,
  dockerLogin,
  dockerLogout,
  fixVersion,
  getDocsUrl,
  getLatestBeta,
  getLatestStable,
  getLatestVersion,
  getReleaseNotes,
  getVersionType,
  isSemVer,
  loadAvailableVersions,
  loadBootstrapCurrentVersion,
  loadCurrentVersion,
  loadLocalVersions,
  sortImages,
  sortVersions,
  getFactoryVersion,
}
