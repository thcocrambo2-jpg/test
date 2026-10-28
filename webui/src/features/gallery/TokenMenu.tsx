import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '@/components/ui'
import type { useImageToken } from '@/components/SettingsToken'
import { canShareFiles } from '@/lib/settingsToken'
import { cx, useMediaQuery } from '@/lib/util'
import { BracesIcon, CheckIcon, CopyIcon, DownloadIcon, ShareIcon } from './icons'
import s from './tokenMenu.module.css'

/*
 * The { } button on a picture: its settings token, to copy, save or share.
 *
 * A menu rather than three buttons because the lightbox already has a
 * Download, for the picture, and a second arrow beside it would be a guess;
 * and because a phone has no room in that header for two more marks.
 *
 * Opening it builds the token (`prepare`), so Share and Download act on one
 * that is already there: Safari opens a share sheet only while the tap still
 * counts, and a recipe fetch plus a deflate after the tap can use that up.
 *
 * Drawn in a portal, fixed to the button, because the output panel's tiles
 * clip what they hold. On a phone it is a sheet from the bottom with
 * thumb-sized rows instead. Clicks and keys are stopped at its edge: React
 * bubbles them out of a portal to the tile, which would open the lightbox.
 */

type Settings = ReturnType<typeof useImageToken>

/** About the menu's height, to decide whether it fits under the button. */
const MENU_HEIGHT = 160

export function TokenMenu({ settings, className }: { settings: Settings; className?: string }) {
  const [open, setOpen] = useState(false)
  const [place, setPlace] = useState<CSSProperties>({})
  const phone = useMediaQuery('(max-width: 720px)')
  // Around the Button, which does not forward a ref.
  const trigger = useRef<HTMLSpanElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const shareable = canShareFiles()

  function close() {
    setOpen(false)
    trigger.current?.querySelector('button')?.focus()
  }

  useLayoutEffect(() => {
    if (!open || phone || !trigger.current) return
    const rect = trigger.current.getBoundingClientRect()
    const right = window.innerWidth - rect.right
    setPlace(
      rect.bottom + MENU_HEIGHT > window.innerHeight
        ? { right, bottom: window.innerHeight - rect.top + 6 }
        : { right, top: rect.bottom + 6 },
    )
  }, [open, phone])

  // Taken in the capture phase and stopped there, as Modal does, so Escape
  // closes this and not the lightbox behind it, and the arrows do not flip
  // the picture under an open menu.
  useEffect(() => {
    if (!open) return
    menu.current?.querySelector('button')?.focus()
    function onKeyDown(event: KeyboardEvent) {
      event.stopPropagation()
      if (event.key === 'Escape') close()
    }
    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node
      if (!menu.current?.contains(target) && !trigger.current?.contains(target)) setOpen(false)
    }
    const shut = () => setOpen(false)
    window.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('resize', shut)
    window.addEventListener('scroll', shut, true)
    return () => {
      window.removeEventListener('keydown', onKeyDown, true)
      document.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('resize', shut)
      window.removeEventListener('scroll', shut, true)
    }
  }, [open])

  function toggle() {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    void settings.prepare().then((token) => {
      if (token === null) setOpen(false)
    })
  }

  const ready = settings.token !== null
  const items = (
    <>
      <div className={s.head}>
        Settings token
        <span>{ready ? `${settings.token!.length.toLocaleString()} characters` : '…'}</span>
      </div>
      <button
        type="button"
        role="menuitem"
        className={s.item}
        onClick={() => {
          setOpen(false)
          void settings.copy()
        }}
      >
        <CopyIcon />
        Copy token
      </button>
      <button
        type="button"
        role="menuitem"
        className={s.item}
        disabled={!ready}
        onClick={() => {
          setOpen(false)
          settings.download()
        }}
      >
        <DownloadIcon />
        Download token
        <small>.txt</small>
      </button>
      {shareable && (
        <button
          type="button"
          role="menuitem"
          className={s.item}
          disabled={!ready}
          onClick={() => {
            setOpen(false)
            void settings.share()
          }}
        >
          <ShareIcon />
          Share token…
          <small>.txt</small>
        </button>
      )}
    </>
  )

  const stop = {
    onClick: (event: { stopPropagation: () => void }) => event.stopPropagation(),
    onKeyDown: (event: { stopPropagation: () => void }) => event.stopPropagation(),
  }

  return (
    <>
      {/* Its keys stay here too: Enter on a tile's button would open the tile. */}
      <span ref={trigger} className={s.anchor} onKeyDown={(event) => event.stopPropagation()}>
      <Button
        size="sm"
        variant="ghost"
        className={cx(s.trigger, open && s.triggerOpen, className)}
        loading={settings.busy && !open}
        onClick={(event) => {
          event.stopPropagation()
          toggle()
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Settings token"
        title={settings.copied ? 'Settings token copied' : 'Settings token: copy, download or share'}
      >
        {settings.copied ? <CheckIcon /> : <BracesIcon />}
        <span className={s.caret} aria-hidden>
          ▼
        </span>
      </Button>
      </span>
      {open &&
        createPortal(
          phone ? (
            <div className={s.scrim} {...stop}>
              <div ref={menu} className={s.sheet} role="menu" aria-label="Settings token">
                <div className={s.grab} aria-hidden />
                {items}
                <button type="button" className={s.cancel} onClick={() => setOpen(false)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div ref={menu} className={s.menu} style={place} role="menu" aria-label="Settings token" {...stop}>
              {items}
            </div>
          ),
          document.body,
        )}
    </>
  )
}
