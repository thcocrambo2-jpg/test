# Lessons for Z-Image Turbo + Default preset

What earlier images taught about getting this model and preset to match a
reference. Every writer reads this before its first prompt, and adds to it
when its image is finished. General rules only — nothing about one
particular subject. Review it now and then and move what holds up into
the base prompt.

- (A/B test, 2026-10-24) A long, detailed prompt (~540 words, the two-field format from base_prompt.md) beat a 300-word one written to vendor "80-250 words" advice: 5.5 vs 4.0. The shorter one lost the pose, the outfit and the body. Keep detail; do not trim to a word budget.
- Z-Image has no negative prompt, so a negated noun can bring in the very thing it names. "No button placket, no row of small buttons" kept producing a row of buttons. Describing the area positively ("smooth, unbroken, seamless panel of fabric") and calling the one fastener something else ("one big round wooden ball, like a large bead") removed the row. Leave the unwanted noun out of the prompt entirely.
- Makeup buried in the middle of the prompt was ignored and the face came out bare. Short makeup phrases at the very start of the description ("bold black kohl on the lower waterline", "mauve-rose lipstick") brought makeup in, but only partly. "Glamorous Indian woman wearing full makeup" also added an unrequested bindi; "bare, clear forehead, unmarked skin between the brows" took it out.
- Face width barely responds to wording. Long-oval and slim-cheek words, putting them first, and shape cues (V-shaped face, 1.5× long as wide, hollows under the cheekbones) all left the model's default rounder, wider face. Don't spend many iterations on face width; fix other categories first.
