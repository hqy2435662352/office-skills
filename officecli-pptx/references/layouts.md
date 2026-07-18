# Layout Recipes

Patterns not obvious from the primitives. Each gives the **visual outcome** first, then a runnable block. `$FILE` = your filename. Use `/slide[last()]` to address the slide you just added. The recipes demonstrate **structure and coordinate math** — swap in the palette / fonts you chose for this topic; the navy `1E2761` + Georgia is just the example's theme, not a house style to copy verbatim.

**Z-order.** Later-added shapes are on top. Add background decoration FIRST, titles LAST. To fix after the fact: `--prop zorder=back/front` (renumbers siblings — re-`get --depth 1` before stacking more).

## (a) Cover (and section divider)

**Visual outcome.** Dark navy fill, centered 44pt title, 18pt ice-blue meta line.

```bash
officecli add "$FILE" / --type slide --prop layout=blank --prop background=1E2761
officecli add "$FILE" "/slide[last()]" --type shape --prop text="Strategic Growth Review" \
  --prop x=2cm --prop y=7cm --prop width=29.87cm --prop height=3cm \
  --prop font=Georgia --prop size=44 --prop bold=true --prop color=FFFFFF --prop align=center
officecli add "$FILE" "/slide[last()]" --type shape --prop text="Prepared for Acme Leadership — FY26 Outlook" \
  --prop x=2cm --prop y=11cm --prop width=29.87cm --prop height=1.2cm \
  --prop font=Calibri --prop size=18 --prop color=CADCFC --prop align=center
```

**Section divider** = same cover, plus a giant translucent number (`size=120`, `opacity=0.15`) added FIRST so it sits behind the section title.

## (b) Data slide (chart + commentary block)

**Visual outcome.** Left two-thirds: column chart with brand series colors. Right one-third: "Key Insight" card with 20pt heading + 18pt body — audience reads the takeaway before parsing the bars.

```bash
officecli add "$FILE" / --type slide --prop layout=blank --prop background=FFFFFF
officecli add "$FILE" "/slide[last()]" --type shape --prop text="FY26 Revenue Beat Plan by 18%" \
  --prop x=1.5cm --prop y=1cm --prop width=30cm --prop height=1.8cm \
  --prop font=Georgia --prop size=36 --prop bold=true --prop color=1E2761

# Chart — left 2/3 (single-quote the title because of `$`)
officecli add "$FILE" "/slide[last()]" --type chart --prop chartType=column \
  --prop series1.name=Actual --prop series1.values="42,45,48,55" --prop series1.color=1E2761 \
  --prop series2.name=Plan --prop series2.values="40,42,45,48" --prop series2.color=CADCFC \
  --prop categories="Q1,Q2,Q3,Q4" --prop x=1.5cm --prop y=3.5cm --prop width=20cm --prop height=14cm --prop title='FY26 Revenue ($M)'

# Commentary card — right 1/3: background + heading + body
officecli add "$FILE" "/slide[last()]" --type shape --prop preset=roundRect --prop fill=F5F7FA --prop line=none \
  --prop x=22.5cm --prop y=3.5cm --prop width=9.8cm --prop height=14cm
officecli add "$FILE" "/slide[last()]" --type shape --prop text="Key Insight" \
  --prop x=23cm --prop y=4cm --prop width=9cm --prop height=1.2cm \
  --prop font=Georgia --prop size=20 --prop bold=true --prop color=1E2761
officecli add "$FILE" "/slide[last()]" --type shape --prop text="EMEA launch + NRR at 118% drove 12pp of the 18pp beat." \
  --prop x=23cm --prop y=5.5cm --prop width=9cm --prop height=11cm \
  --prop font=Calibri --prop size=18 --prop color=333333
```

## (c) Flowchart / process diagram (boxes + connectors)

**Visual outcome.** Four rounded boxes across at y=8cm, each 6×3cm, alternating navy/iceblue, joined by elbow connectors with triangle arrowheads.

Grid math (4 boxes, 33.87cm slide, 1.5cm margins): `gap = (33.87 − 3 − 24) / 3 = 2.29cm`. x-positions: `1.5, 9.79, 18.08, 26.37`.

Each box carries its own label via `valign=middle` (no separate overlay shape needed). Use `batch` heredoc for portable coordinate arithmetic.

```bash
cat <<EOF | officecli batch "$FILE"
[
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"name":"Step1","preset":"roundRect","fill":"1E2761","line":"none","x":"1.5cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 1","font":"Georgia","size":"20","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"name":"Step2","preset":"roundRect","fill":"CADCFC","line":"none","x":"9.79cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 2","font":"Georgia","size":"20","bold":"true","color":"1E2761","align":"center","valign":"middle"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"name":"Step3","preset":"roundRect","fill":"1E2761","line":"none","x":"18.08cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 3","font":"Georgia","size":"20","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"name":"Step4","preset":"roundRect","fill":"CADCFC","line":"none","x":"26.37cm","y":"8cm","width":"6cm","height":"3cm","text":"Step 4","font":"Georgia","size":"20","bold":"true","color":"1E2761","align":"center","valign":"middle"}}
]
EOF

# Connector pattern — reuse for any box-to-box graph.
for pair in "Step1 Step2" "Step2 Step3" "Step3 Step4"; do
  A=${pair% *}; B=${pair#* }
  officecli add "$FILE" "/slide[$SLIDE]" --type connector \
    --prop "from=/slide[$SLIDE]/shape[@name=$A]" \
    --prop "to=/slide[$SLIDE]/shape[@name=$B]" \
    --prop shape=elbow --prop color=333333 --prop tailEnd=triangle
done
```

`shape=elbow` is canonical (`bentConnector2` / `bentConnector3` also accepted).

## (d) Multi-slide deck skeletons

No code block — it's a rhythm. The sequences below are **illustrations of one working cadence (alternating dark dividers with white content), not required running orders** — derive your actual arc from the content first (see "Title sequence first" in main SKILL.md), then borrow whatever divider/content rhythm fits:

- **10-slide review:** Cover · Agenda · 3 KPI · Div01 · Chart · Chart · Div02 · Flow · Timeline · Close
- **20-slide pitch:** same rhythm × 2, sectioned Problem · Solution · Market · Product · Traction · Model · Team · Financials · Ask
- Every divider must appear **before** its section content (Gate 3 order sanity)
- Cover/divider = (a); chart pages = (b); process pages = (c); KPI pages = (e); decision pages = (f)

## (e) KPI callouts — giant-number card grid

**Visual outcome.** Three or four giant numbers across a row; each card = unit sublabel + small percent-change chip + one-line takeaway. The single most common exec-deck element.

**Sizing rule.** 60pt Georgia bold fits ~5 chars in a 9.78cm card (`$84.2`, `118%`, `24.5`). For longer values (`$84.2M`), split: `$84.2` as the big number, `USD millions` as the sublabel — never shrink the font to chase a unit suffix, it just wraps.

Grid math (3 cards, 1.5cm margins, 0.76cm gap): `col_width = (33.87 − 3 − 1.52) / 3 = 9.78cm`. x-positions: `1.5, 12.04, 22.58`. Use accent color on a single "watch" card so risk reads in one second.

```bash
# Two cards: navy standard + terracotta watch. Each = bg + big number + sublabel + chip.
cat <<EOF | officecli batch "$FILE"
[
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"preset":"roundRect","fill":"1E2761","line":"none","x":"1.5cm","y":"4cm","width":"9.78cm","height":"7cm"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"text":"84.2","x":"1.5cm","y":"4.8cm","width":"9.78cm","height":"2.8cm","font":"Georgia","size":"60","bold":"true","color":"FFFFFF","align":"center"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"text":"USD millions · ARR","x":"1.5cm","y":"8cm","width":"9.78cm","height":"0.8cm","font":"Calibri","size":"14","color":"CADCFC","align":"center"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"text":"+24% YoY","x":"1.5cm","y":"9cm","width":"9.78cm","height":"0.8cm","font":"Calibri","size":"14","bold":"true","color":"CADCFC","align":"center"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"preset":"roundRect","fill":"B85042","line":"none","x":"22.58cm","y":"4cm","width":"9.78cm","height":"7cm"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"text":"\$1.42","x":"22.58cm","y":"4.8cm","width":"9.78cm","height":"2.8cm","font":"Georgia","size":"60","bold":"true","color":"FFFFFF","align":"center"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"text":"CAC payback (yrs)","x":"22.58cm","y":"8cm","width":"9.78cm","height":"0.8cm","font":"Calibri","size":"14","color":"FFFFFF","align":"center"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"text":"+8% — watch","x":"22.58cm","y":"9cm","width":"9.78cm","height":"0.8cm","font":"Calibri","size":"14","bold":"true","color":"FFFFFF","align":"center"}}
]
EOF
```

## (f) Decision tree — YES/NO branching

**Visual outcome.** Diamond at top-center; YES/NO child boxes diverging left-right; both converge into a shared terminal box. Layout: diamond at `x=13.94, y=2cm, 6×3cm`; YES at `3cm, 7.5cm`; NO at `22.87cm, 7.5cm`; terminal at `13.94cm, 13cm`. Convention: red = stop/escalate, blue = standard, green = safe terminal. **Every connector needs an arrowhead** — readers misparse direction otherwise.

```bash
cat <<EOF | officecli batch "$FILE"
[
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"name":"Decide","preset":"diamond","fill":"1E2761","line":"none","x":"13.94cm","y":"2cm","width":"6cm","height":"3cm","text":"Hazardous energy present?","font":"Calibri","size":"14","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"name":"YesBox","preset":"roundRect","fill":"B85042","line":"none","x":"3cm","y":"7.5cm","width":"8cm","height":"3cm","text":"Lockout + Tagout + Verify","font":"Calibri","size":"16","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"name":"NoBox","preset":"roundRect","fill":"CADCFC","line":"none","x":"22.87cm","y":"7.5cm","width":"8cm","height":"3cm","text":"Proceed with standard PPE","font":"Calibri","size":"16","bold":"true","color":"1E2761","align":"center","valign":"middle"}},
  {"command":"add","parent":"/slide[$SLIDE]","type":"shape","props":{"name":"Done","preset":"roundRect","fill":"2C5F2D","line":"none","x":"13.94cm","y":"13cm","width":"6cm","height":"2.5cm","text":"Begin service","font":"Calibri","size":"16","bold":"true","color":"FFFFFF","align":"center","valign":"middle"}}
]
EOF
```

Then 4 connectors (`Decide→YesBox`, `Decide→NoBox`, `YesBox→Done`, `NoBox→Done`) using the connector loop pattern from (c).
