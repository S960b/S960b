function stage34ebPilotTaskIds(options) {
  const ids = options && options.task_ids;
  // Fail closed unless EXACTLY ONE explicit managed ID is supplied.
  if (!Array.isArray(ids) || ids.length !== 1 ||
      typeof ids[0] !== "string" ||
      !/^task_[0-9a-f]{32}$/.test(ids[0]) ||
      ids[0] === "task_f2c64cfdf7894537b35a19a6def977c6")
    return Object.freeze([]);
  return Object.freeze([ids[0]]);
}
