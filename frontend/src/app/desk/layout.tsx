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
  },
  twitter: {
    card: 'summary_large_image',
    title: 'The Desk · Epoch Labs',
    description: DESK_HERO.intro,
  },
};

export default function DeskLayout({ children }: LayoutProps<'/desk'>) {
  return children;
}
