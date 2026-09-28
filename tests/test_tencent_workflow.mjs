import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../web/js/las_video_upload.js", import.meta.url), "utf8")
  .replace(/^import .*;\r?\n/gm, "");

function loadWorkflow(values) {
  let extension;
  const app = { registerExtension(value) { extension = value; } };
  const document = { createElement: () => ({ style: {}, remove() {} }), body: { append() {} } };
  new Function("app", "api", "document", source)(app, {}, document);
  class Node {
    constructor() {
      this.widgets = [
        { name: "video_url", value: "" },
        { name: "scene", value: "真人" },
        { name: "output_resolution", value: "1080p" },
        { name: "local_video", value: "", options: { values: [""] } },
        { name: "output_base_name", value: "" },
        { name: "resume_task_id", value: "" },
      ];
      this.onNodeCreated();
    }
    addWidget(type, name, value) {
      const widget = { type, name, value, options: {} };
      this.widgets.push(widget);
      return widget;
    }
    onConfigure() { this.configured = true; return "original-result"; }
  }
  extension.beforeRegisterNodeDef(Node, { name: "TencentMPSVideoEnhance" });
  const node = new Node();
  for (let index = 0; index < values.length; index++) node.widgets[index].value = values[index];
  assert.equal(node.onConfigure({ widgets_values: values }), "original-result");
  assert.equal(node.configured, true);
  return node.widgets.map((widget) => widget.value);
}

test("legacy upload button does not become a resume task ID", () => {
  const values = ["", "真人", "1080p", "480p测试.mp4", "", "video"];
  const loaded = loadWorkflow(values);
  assert.deepEqual(loaded.slice(0, 5), values.slice(0, 5));
  assert.equal(loaded[5], "");
});

test("already resaved affected workflow clears the duplicated button value", () => {
  assert.equal(loadWorkflow(["", "真人", "1080p", "480p测试.mp4", "", "video", "video"])[5], "");
});

test("valid recovery IDs and empty new-task inputs remain unchanged", () => {
  for (const task of ["", "2600028868-WorkflowTask-c2284a3085840340b35fc1a8989ff495tt7"]) {
    assert.equal(loadWorkflow(["", "真人", "1080p", "input.mp4", "output", task, "video"])[5], task);
  }
});

test("other invalid IDs are preserved for backend validation instead of creating a new task", () => {
  const task = "eb3d7801-847e-4589-be47-703afe0ab7db";
  assert.equal(loadWorkflow(["", "真人", "1080p", "input.mp4", "", task, "video"])[5], task);
});
