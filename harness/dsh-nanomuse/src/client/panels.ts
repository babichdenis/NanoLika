/** Ids of the global panels NanoLika adds to the harness's `main` seat. */

/** The rooms of the Muse desktop, in the rail's order. */
export const FEED_PANEL = 'nanomuse-feed'
export const IDEAS_PANEL = 'nanomuse-ideas'
export const GOALS_PANEL = 'nanomuse-goals'
export const LIBRARY_PANEL = 'nanomuse-library'

/** The Devices page: this computer on the account's hub and the other devices. */
export const DEVICES_PANEL = 'nanomuse-devices'

/** The preview pane: a page or an artifact the agent puts beside the chat. */
export const PREVIEW_PANEL = 'nanomuse-preview'

export const ROOM_PANELS = [FEED_PANEL, IDEAS_PANEL, GOALS_PANEL, LIBRARY_PANEL, DEVICES_PANEL] as const

/** Where a problem with NanoLika is reported. */
export const SITE_URL = 'https://nanolika.ru'
export const REPO_URL = 'https://github.com/nano-muse/nanoMuse'
export const ISSUES_URL = `${REPO_URL}/issues`
export const DOCS_URL = `${REPO_URL}/tree/main/docs`
export const DISCUSSIONS_URL = `${REPO_URL}/discussions`
export const LICENSE_URL = `${REPO_URL}/blob/main/LICENSE`
