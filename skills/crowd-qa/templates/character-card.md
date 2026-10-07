# Character card format (lanes/<id>/card.md)

Written by the orchestrator before launch and shown to the user for approval. The runner reads the bold
fields by exact name, so keep them as `- **Field**: value` lines. Everything after "## Story" is free text the
tester reads as the person's background: who they are, how they talk, what they care about, how they react
when something goes wrong.

```markdown
# Grace Mensah

- **Name**: Grace Mensah
- **Id**: grace
- **Role**: owner               # the product's own roles (owner | agent | viewer, host | guest, admin | member),
                                #   plus the core three: designer, speedy, boundary
- **Surface**: web              # web | ios | android | cli | api
- **Device**: browser, viewport 1280x800   # or a simulator model, a terminal, "curl"
- **Text size**: medium         # mobile only: content size category
- **Exclusive**: no             # yes = runs alone (only if they stall something shared; avoid)
- **Age / city**: 46, Accra
- **Work**: runs a three-chair hair salon, books clients by phone and chat today
- **Tech comfort**: 2/5. Phone apps and a laptop for invoices, nothing else
- **Language**: English, types some Twi words into messages
- **Patience**: low on busy Saturdays; gives up after the second unclear error
- **Accessibility**: reading glasses; browser zoom 125%
- **Wants on day 1**: sign up, invite her two stylists, set opening hours, take one booking
- **Wants on day 2**: see what her stylists replied to clients and move a booking
- **Relationships**: employs ama and kofi (agents); depends_on: none
- **Email**: grace@crowd.test, password CrowdQA!2026

## Story
Two paragraphs in plain prose: how she got here, what she fears (looking foolish in front of a client, losing
a booking), what she would tell a friend about a product that wasted her evening, the phrases she uses.

## Voice
How she writes a complaint, with two example lines.
```

## Rules for a crowd
- Spread across tech comfort (1-5), age, patience, accessibility (zoom, keyboard only, screen reader names),
  device and surface, the parts of the product, and its roles.
- **The core three, in every crowd:**
  - **designer**: reviews every screen against the project's design source or, without one, against the
    product's own components and consistency. Findings name the rule broken and the fix.
  - **speedy**: never waits. Double-submits, goes back mid-load, closes mid-save, switches tabs fast.
  - **boundary**: tests limits through the product's own screens and endpoints only: other users' records
    through guessed links and ids, what each role may and may not do, odd and oversized input, stale links.
    On the throwaway stack only, stopping at proof.
- Anyone who depends on another character names them in Relationships with `depends_on: <id>`. That character
  runs first on the same day (an agent after their owner, a customer after the business).
- Exclusive characters stop everyone else while they run. Fake a weak network inside the character's own
  browser instead (`agent-browser offline on/off`).
- No two characters own the same core scenario; overlap only where a me-too is expected.
- Names are people, not jobs. Never "User 3".
