/**
 * The share arrow at the top right of a chat: postcards of the face, and the card that
 * carries the account's invite code — a friend who signs up with it gets credit on
 * NanoLika Cloud, and so does the person inviting. Occupies
 * `conversation.session.header.utilities`. It shows in side chats too: a share is not a
 * property of one conversation.
 */
import { createElement as h, useEffect, useState, type ReactNode } from 'react'
import { type Translate } from './api.ts'
import { AvatarShareSheet } from './AvatarShare.tsx'
import { IconShare } from './icons.tsx'
import { useLive } from './live.ts'
import { mainChatId } from './MuseChats.tsx'
import { win } from './win.ts'

export function makeInviteButton(t: Translate) {
  return function InviteButton({ sessionId }: { sessionId?: string | undefined }): ReactNode {
    const live = useLive()
    // the postcards from the corner of the window: a picture of the face, and with an
    // account the one that carries the code (src/client/AvatarShare.tsx)
    const [share, setShare] = useState(false)
    // this seat is per session: it tells the rest of the shell which chat is on screen
    const side = sessionId !== undefined && mainChatId() !== undefined && mainChatId() !== sessionId
    useEffect(() => {
      win.current(sessionId ?? null)
      if (side) document.documentElement.dataset['nmSide'] = ''
      else delete document.documentElement.dataset['nmSide']
      return () => {
        delete document.documentElement.dataset['nmSide']
        win.current(null)
      }
    }, [sessionId, side])
    const name = live.profile.name || t('brand')
    // One control on purpose. The code and the credit a friend gets are already in the
    // invite card of this sheet, so a second "Invite" button only repeated them; the
    // tally of who came and what it earned lives in the account page.
    return h('span', null,
      h('button', { type: 'button', className: 'nm-invite nm-invite-icon', title: t('acShareTitle'), 'aria-label': t('acShareTitle'), onClick: () => setShare(true) }, h(IconShare, { size: 15 })),
      share ? h(AvatarShareSheet, { t, name, onClose: () => setShare(false) }) : null)
  }
}
