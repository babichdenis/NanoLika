/**
 * Search that works without buying anything: the relay's own free backends.
 *
 * The harness ships one search provider and it is DeepSeek's, which needs a DeepSeek
 * account or an API key. A person signed in to NanoLika Cloud has neither, so `web_search`
 * failed for them. This registers a second provider with the same `ctx.web` service, asking
 * the relay's `/v1/search` (cloud/nanomuse_cloud/search.py), which searches SearXNG, then
 * DuckDuckGo, then Wikipedia, then GitHub, and charges nothing.
 *
 * The harness picks a provider by id when one is configured and otherwise takes the single
 * usable one, so this registers only while the person is signed in; with an account it is
 * the provider every session searches through.
 *
 * The seam's types live in `@deepseek-ai/dsh-web`, which the bundle does not depend on: the
 * host loads us inside its own tree. So the two shapes we touch are restated here, narrowly.
 */
import type { Context } from '@deepseek-ai/cordis'
import type {} from './cloud.ts'

export const name = 'nanomuse-web-search'
export const inject = ['web', 'nanomuseCloud']

export const PROVIDER_ID = 'nanomuse-relay'

/** `WebSearchRequest`, as far as a search provider reads it. */
interface SearchRequest {
  readonly query: string
  readonly maxResults?: number
}

/** `WebSearchSource`. */
interface SearchSource {
  url: string
  title?: string
  snippet?: string
}

/** `WebSearchProvider`. */
interface SearchProvider {
  id: string
  available(): boolean
  search(request: SearchRequest, signal?: AbortSignal): Promise<{ sources: SearchSource[]; truncated: boolean }>
}

/** The one member of the web service this plugin uses. */
interface WebService {
  registerSearchProvider(provider: SearchProvider): () => void
}

export function apply(ctx: Context): void {
  const web = (ctx as unknown as { web?: WebService }).web
  if (!web) return
  ctx.effect(
    () =>
      web.registerSearchProvider({
        id: PROVIDER_ID,
        // A local check only, never a network call: the seam asks this to pick a backend.
        available: () => ctx.nanomuseCloud.signedIn,
        async search(request, signal) {
          const limit = request.maxResults ?? 8
          const answer = await ctx.nanomuseCloud.search(request.query, limit, signal)
          return {
            sources: answer.sources.slice(0, limit).map((s) => ({ url: s.url, title: s.title, snippet: s.snippet })),
            truncated: answer.sources.length > limit,
          }
        },
      }),
    'nanomuse web search: the relay as a provider',
  )
}
