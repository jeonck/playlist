---
title: Playlist
layout: hextra-home
---

{{< hextra/hero-headline >}}
Turn YouTube into&nbsp;<br class="sm:hx-block hx-hidden" />English study notes
{{< /hextra/hero-headline >}}

{{< hextra/hero-subtitle >}}
Submit a YouTube link, a maintainer approves it, and Claude turns the video&rsquo;s
transcript into idioms, vocabulary, natural phrasing, and a quiz — organized by
category in the sidebar.
{{< /hextra/hero-subtitle >}}

{{< hextra/hero-button text="Browse categories" link="docs" >}}

{{< hextra/feature-grid >}}
  {{< hextra/feature-card
    title="Submit a video"
    subtitle="Open an issue with a YouTube link. Anyone can request — a maintainer reviews before it's processed."
    link="https://github.com/jeonck/playlist/issues/new?template=youtube-request.yml"
    icon="youtube"
  >}}
  {{< hextra/feature-card
    title="Maintainer approves"
    subtitle="Adding the `approved` label triggers the pipeline — only repo collaborators can do this."
    icon="shield-check"
  >}}
  {{< hextra/feature-card
    title="Auto-organized by category"
    subtitle="Claude classifies each post into a topic and it appears in the matching sidebar section automatically."
    link="docs"
    icon="collection"
  >}}
{{< /hextra/feature-grid >}}
