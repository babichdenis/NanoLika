/**
 * What the side pane can show, and the route that serves a file to it. Kept apart
 * from `preview-file.ts` because the browser half needs the same answers and
 * cannot import Node's `fs`: one list of kinds, one URL shape, two runtimes.
 */

/** Where the browser asks for a file the agent wants shown. */
export const PREVIEW_FILE_PREFIX = '/nanomuse/file'

/** How the pane draws a file: the element the browser half picks for this kind. */
export type PreviewFileKind = 'video' | 'audio' | 'image' | 'pdf' | 'text'

/** The pane's kind for a path, by its extension; undefined for a file it cannot show. */
export function kindOfFile(path: string): PreviewFileKind | undefined {
  const ext = /\.([a-z0-9]+)$/i.exec(path)?.[1]?.toLowerCase() ?? ''
  switch (ext) {
    case 'mp4':
    case 'm4v':
    case 'webm':
    case 'mov':
      return 'video'
    case 'mp3':
    case 'm4a':
    case 'wav':
    case 'ogg':
    case 'oga':
    case 'opus':
    case 'flac':
      return 'audio'
    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
    case 'webp':
    case 'avif':
      return 'image'
    case 'pdf':
      return 'pdf'
    case 'txt':
    case 'md':
      return 'text'
    default:
      return undefined
  }
}

/** The URL the pane loads for a file on this computer. */
export function previewFileUrl(path: string): string {
  return `${PREVIEW_FILE_PREFIX}?path=${encodeURIComponent(path)}`
}
