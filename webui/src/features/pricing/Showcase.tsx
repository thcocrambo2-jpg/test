import { useState } from 'react'
import type { ShowcaseBlock, ShowcaseMedia, ShowcaseSection } from '@/api/types'
import { Pill } from '@/components/ui'
import { cx } from '@/lib/util'
import { MediaLightbox } from './MediaLightbox'
import s from './pricing.module.css'

/*
 * The feature showcase, rendered from JSON.
 *
 * `showcase.showcase()` already returns structured dataclasses — Showcase →
 * Section → Block → Media — and `showcase_html` (theme.py:3827) walks that
 * tree emitting several hundred lines of string-concatenated HTML because
 * that is the only thing `gr.HTML` accepts. Here the same tree is rendered by
 * components, and the recursion that `Block.blocks` was designed for (a split
 * putting prose beside a compare; a pair sitting two compares side by side)
 * is a recursive component instead of nested f-strings.
 *
 * The lightbox is a real one. The Gradio version is CSS-only, and the comment
 * at theme.py:1724 says why: `gr.HTML` strips `<script>`.
 */
export function ShowcaseSections({ sections }: { sections: ShowcaseSection[] }) {
  const [viewing, setViewing] = useState<ShowcaseMedia | null>(null)

  return (
    <>
      {sections.map((section) => (
        <section
          key={section.key}
          className={cx(s.showcaseSection, section.locked && s.locked)}
        >
          <div className={s.showcaseHead}>
            <div>
              <div className={s.showcaseLabel}>
                <span>{section.label}</span>
                {section.locked && <Pill tone="warning">not in your plan</Pill>}
              </div>
              <h3 className={s.headline}>{section.headline}</h3>
              <div className={s.body}>
                {section.body.map((paragraph, index) => (
                  <p key={index}>{paragraph}</p>
                ))}
              </div>
            </div>
            {section.highlights.length > 0 && (
              <div className={s.highlights}>
                {section.highlights.map((highlight) => (
                  <Pill key={highlight}>{highlight}</Pill>
                ))}
              </div>
            )}
          </div>

          {section.blocks.length > 0 && (
            <div className={s.blocks}>
              {section.blocks.map((block, index) => (
                <Block key={index} block={block} onView={setViewing} />
              ))}
            </div>
          )}
        </section>
      ))}

      {viewing && <MediaLightbox media={viewing} onClose={() => setViewing(null)} />}
    </>
  )
}

function Block({
  block,
  onView,
}: {
  block: ShowcaseBlock
  onView: (media: ShowcaseMedia) => void
}) {
  const children = block.blocks.map((child, index) => (
    <Block key={index} block={child} onView={onView} />
  ))

  const items = block.items.map((item, index) => (
    <Media key={`${item.path}-${index}`} media={item} onView={onView} />
  ))

  switch (block.type) {
    case 'split':
      return (
        <div className={cx(s.split, block.reverse && s.splitReverse)}>
          <div>
            {block.title && <div className={s.blockTitle}>{block.title}</div>}
            {block.body && <p className={s.body}>{block.body}</p>}
            {block.note && <div className={s.blockNote}>{block.note}</div>}
          </div>
          <div className={s.blocks}>{children.length > 0 ? children : items}</div>
        </div>
      )

    case 'pair':
      return <div className={s.pair}>{children.length > 0 ? children : items}</div>

    case 'fan':
      return (
        <div>
          {block.title && <div className={s.blockTitle}>{block.title}</div>}
          <div className={s.fan}>
            {block.source && <Media media={block.source} onView={onView} />}
            <div className={s.fanItems}>{items}</div>
          </div>
          {block.note && <div className={s.blockNote}>{block.note}</div>}
        </div>
      )

    case 'strip':
      return (
        <div>
          {block.title && <div className={s.blockTitle}>{block.title}</div>}
          <div className={s.strip}>{items}</div>
          {block.note && <div className={s.blockNote}>{block.note}</div>}
        </div>
      )

    case 'collage':
      return (
        <div>
          {block.title && <div className={s.blockTitle}>{block.title}</div>}
          <div className={s.collage}>{items}</div>
          {block.note && <div className={s.blockNote}>{block.note}</div>}
        </div>
      )

    case 'compare':
      return (
        <div>
          {block.title && <div className={s.blockTitle}>{block.title}</div>}
          <div className={s.compare}>{items}</div>
          {block.note && <div className={s.blockNote}>{block.note}</div>}
        </div>
      )

    // hero, shot, row and anything a newer showcase.json invents.
    default:
      return (
        <div>
          {block.title && <div className={s.blockTitle}>{block.title}</div>}
          <div className={s.row}>{items}</div>
          {block.note && <div className={s.blockNote}>{block.note}</div>}
        </div>
      )
  }
}

function Media({
  media,
  onView,
}: {
  media: ShowcaseMedia
  onView: (media: ShowcaseMedia) => void
}) {
  const missing = media.url === null
  return (
    <figure
      className={s.media}
      style={{
        ['--ratio' as string]: media.ratio?.replace('/', ' / ') ?? '4 / 3',
        ['--hue' as string]: String(media.hue),
      }}
      role="button"
      tabIndex={0}
      onClick={() => !missing && onView(media)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' && !missing) onView(media)
      }}
    >
      {missing ? (
        <div className={s.placeholder}>{media.path}</div>
      ) : media.is_video ? (
        <video className={s.mediaImage} src={media.url!} muted loop playsInline autoPlay />
      ) : (
        <img
          className={s.mediaImage}
          src={media.url!}
          alt={media.caption ?? media.label ?? ''}
          loading="lazy"
        />
      )}
      {media.label && (
        <span className={cx(s.mediaLabel, media.accent && s.mediaLabelAccent)}>
          {media.label}
        </span>
      )}
      {media.caption && <figcaption className={s.mediaCaption}>{media.caption}</figcaption>}
    </figure>
  )
}
