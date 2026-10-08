import type { Metadata } from 'next';
import { DESK_HERO } from '@/config/deskCopy';

export const metadata: Metadata = {
  title: 'The Desk · Epoch Labs',
  description: `${DESK_HERO.subtitle} ${DESK_HERO.intro}`,
  alternates: { canonical: '/desk' },
  openGraph: {
    title: 'The Desk · Epoch Labs',
    description: DESK_HERO.intro,
    url: '/desk',
    siteName: 'Epoch Labs',
    type: 'website',
    images: [{ url: '/epoch-logo.png', width: 512, height: 512, alt: 'Epoch Labs' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'The Desk · Epoch Labs',
    description: DESK_HERO.intro,
    images: ['/epoch-logo.png'],
    creator: '@EpochLabsHQ',
  },
};

export default function DeskLayout({ children }: LayoutProps<'/desk'>) {
  return children;
}
