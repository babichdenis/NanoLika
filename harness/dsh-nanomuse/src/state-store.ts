import { randomUUID } from 'node:crypto'
import { mkdir, open, rename, rm, type FileHandle } from 'node:fs/promises'
import { dirname } from 'node:path'

/** One ordered stream of durable, atomic snapshots; a failed write never poisons later writes. */
export class StateStore {
  private queue: Promise<void> = Promise.resolve()

  constructor(private readonly replace: (path: string, contents: string) => Promise<void> = atomicReplace) {}

  write(path: string, value: unknown): Promise<void> {
    // Snapshot before queuing: mutable in-memory state must not leak into an older write.
    const contents = JSON.stringify(value, null, 2) + '\n'
    const current = this.queue.then(() => this.replace(path, contents))
    this.queue = current.catch(() => undefined)
    return current
  }
}

export async function atomicReplace(path: string, contents: string, renameFile: typeof rename = rename, syncDirectory = syncDirectoryAt, syncFile = syncFileHandle): Promise<void> {
  await mkdir(dirname(path), { recursive: true })
  const temporary = `${path}.${randomUUID()}.tmp`
  try {
    const file = await open(temporary, 'wx', 0o600)
    try {
      await file.writeFile(contents, 'utf8')
      await syncFile(file)
    } finally {
      await file.close()
    }
    await renameFile(temporary, path)
    // Make the rename durable as well as the bytes: acknowledgement to the
    // sync engine must wait for both fsyncs before any message is sent.
    await syncDirectory(dirname(path))
  } finally {
    await rm(temporary, { force: true }).catch(() => undefined)
  }
}

async function syncDirectoryAt(path: string): Promise<void> {
  const directory = await open(path, 'r')
  try {
    await directory.sync()
  } finally {
    await directory.close()
  }
}

async function syncFileHandle(file: FileHandle): Promise<void> {
  await file.sync()
}
