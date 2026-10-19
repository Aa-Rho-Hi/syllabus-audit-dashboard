// Browser export of Simple Syllabus syllabi, for when Python can't reach the site but your browser can.
// 1. Log in at https://tamu.simplesyllabus.com  2. Open DevTools > Console  3. Paste this, press Enter.
// It saves simplesyllabus-<SUBJECT>-<status>.json to Downloads. Then:
//    python -m reviewer.import_export ~/Downloads/simplesyllabus-ECEN-awaiting_approval.json
// Only fields the audit needs are kept (no account IDs / UINs). Delete the file afterwards.
(async () => {
  const SUBJECT = "ECEN", STATUS = "awaiting_approval";   // edit these
  const q = new URLSearchParams({ subject_name: SUBJECT, page_size: "1000", page: "0" });
  q.append("statuses[]", STATUS); q.append("term_statuses[]", "current"); q.append("term_statuses[]", "future");
  const list = await fetch("/api2/doc-public?" + q).then(r => r.json());
  const docs = (list.items || []).filter(d => d.entity_type === "section" && d.status === STATUS);
  const C = c => c ? { name: c.name, value: c.value, is_deleted: c.is_deleted, is_visible: c.is_visible,
    is_name_visible: c.is_name_visible, sort_order: c.sort_order,
    meta: c.meta && c.meta.column_type ? { column_type: c.meta.column_type } : null } : null;
  const V = v => v ? { variation_status: v.variation_status, combined: C(v.combined) } : null;
  const out = [];
  for (const [i, d] of docs.entries()) {
    console.log(`${i + 1}/${docs.length} ${d.title}`);
    const fp = await fetch("/api2/doc-full-page-get?code=" + encodeURIComponent(d.code)).then(r => r.json()).catch(() => ({}));
    const dd = fp?.items?.[0]?.doc_data || {};
    const props = {};
    for (const [k, v] of Object.entries(dd.properties || {}))
      if (/^ca_\d+$|^name$|^title$|^subject_name$|^course_number$|^full_name$/.test(k) && k !== "ca_7") props[k] = v;
    const hq = new URLSearchParams({ entity_id: d.entity_id, term_id: d.term_id, family_name: "syllabus" });
    const hj = await fetch("/api2/heading-component?" + hq).then(r => r.json()).catch(() => ({ items: [] }));
    const headings = (hj.items || []).map(x => ({
      component_type: x.component_type, variations: (x.variations || []).map(V),
      columns: (x.columns || []).map(c => ({ heading: { id: c.heading?.id }, variation: V(c.variation) })),
      rows: (x.rows || []).map(r => ({ variation: V(r.variation),
        context: r.context ? { full_name: r.context.full_name, email: r.context.email } : null,
        cells: (r.cells || []).map(c => ({ column_id: c.column_id, variation: V(c.variation) })) })) }));
    out.push({ listing: { code: d.code, title: d.title, term_name: d.term_name, status: d.status,
        entity_id: d.entity_id, term_id: d.term_id, course_number: d.course_number, subject_name: d.subject_name },
      doc: { code: dd.code || d.code, title: dd.title || d.title, sub_title: dd.sub_title || "", status: dd.status || d.status,
        term: { name: d.term_name }, is_missing_required_content: dd.is_missing_required_content,
        modified: dd.modified, properties: props, components: [] },
      headings });
    await new Promise(z => setTimeout(z, 200));   // be gentle with their server
  }
  const blob = new Blob([JSON.stringify({ pulled: new Date().toISOString(), count: out.length, docs: out })],
    { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = `simplesyllabus-${SUBJECT}-${STATUS}.json`;
  document.body.appendChild(a); a.click();
  console.log(`Saved ${out.length} syllabi.`);
})();
