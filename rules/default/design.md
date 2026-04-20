# Criteria: UI/UX Designer
PASS_THRESHOLD: 7
WEIGHTS: completeness=0.35 format=0.25 quality=0.40

## Completeness (what must exist)
- [ ] Design System has: colors (hex), typography, spacing, border radius
- [ ] Wireframe for at least 3 main screens
- [ ] Each wireframe has all states: loading / empty / error / success
- [ ] User flow from entry to exit
- [ ] Component specs with dimensions + states

## Format (structure)
- [ ] Colors listed with name + hex + usage context
- [ ] Wireframe uses ASCII layout with clear borders
- [ ] Navigation explicit: "from X → to Y"
- [ ] Component states enumerated fully

## Quality (depth / usefulness)
- [ ] Design system enough for Dev to implement without asking for colors/sizes
- [ ] Wireframe conveys real layout, not just placeholder text
- [ ] Empty state and error state designed (not skipped)
- [ ] Touch targets visibly large enough (≥ 44pt)
