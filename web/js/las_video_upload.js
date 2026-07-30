import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";


const VIDEO_MIME_TYPES = [
  "video/mp4",
  "video/webm",
  "video/x-matroska",
  "video/quicktime",
  "video/x-msvideo",
  "video/x-flv",
  "video/x-ms-wmv",
  "image/gif",
];


async function getAuthHeader() {
  try {
    const authStore = await api.getAuthStore?.();
    return authStore ? await authStore.getAuthHeader() : null;
  } catch (error) {
    console.warn("Failed to get ComfyUI auth header:", error);
    return null;
  }
}


async function uploadFile(file, progressCallback) {
  const body = new FormData();
  body.append("image", file);

  const response = await new Promise((resolve) => {
    const request = new XMLHttpRequest();
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        progressCallback?.(event.loaded / event.total);
      }
    };
    request.onload = () => resolve(request);
    request.onerror = () => resolve(request);
    request.open("POST", api.apiURL("/upload/image"), true);
    getAuthHeader().then((headers) => {
      headers ??= {};
      for (const key in headers) {
        request.setRequestHeader(key, headers[key]);
      }
      request.send(body);
    });
  });

  if (response.status !== 200) {
    throw new Error(`${response.status} - ${response.statusText || response.responseText}`);
  }
  return JSON.parse(response.responseText).name;
}


function addLocalVideoUploadButton(node) {
  const localVideoWidget = node.widgets?.find((widget) => widget.name === "local_video");
  if (!localVideoWidget || localVideoWidget.__videoEnhanceUploadButtonAdded) {
    return;
  }
  localVideoWidget.__videoEnhanceUploadButtonAdded = true;

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = VIDEO_MIME_TYPES.join(",");
  fileInput.style.display = "none";

  const setWidgetValue = (filename) => {
    if (!localVideoWidget.options.values.includes(filename)) {
      localVideoWidget.options.values.push(filename);
    }
    localVideoWidget.value = filename;
    localVideoWidget.callback?.(filename);
  };

  fileInput.onchange = async () => {
    if (!fileInput.files?.length) {
      return;
    }
    try {
      const filename = await uploadFile(fileInput.files[0], (progress) => {
        node.progress = progress;
      });
      setWidgetValue(filename);
      app.graph.setDirtyCanvas(true, true);
    } catch (error) {
      alert(`Upload video failed: ${error.message || error}`);
    } finally {
      node.progress = undefined;
      fileInput.value = "";
    }
  };

  document.body.append(fileInput);
  node.onRemoved = ((original) => function (...args) {
    fileInput.remove();
    return original?.apply(this, args);
  })(node.onRemoved);

  const uploadWidget = node.addWidget("button", "choose video to upload", "video", () => {
    app.canvas.node_widget = null;
    fileInput.click();
  });
  uploadWidget.options.serialize = false;
}


function setWidgetVisible(node, widget, visible) {
  if (!widget) {
    return;
  }
  widget.__originalComputeSize ??= widget.computeSize;
  widget.hidden = !visible;
  widget.computeSize = visible ? widget.__originalComputeSize : () => [0, -4];
  widget.computedHeight = visible ? undefined : 0;
  node.setSize([node.size[0], node.computeSize()[1]]);
}


function configureMediaKitVersionWidgets(node) {
  const versionWidget = node.widgets?.find((widget) => widget.name === "tool_version");
  const sceneWidget = node.widgets?.find((widget) => widget.name === "scene");
  const bitDepthWidget = node.widgets?.find((widget) => widget.name === "bit_depth");
  if (!versionWidget || !sceneWidget || !bitDepthWidget) {
    return;
  }

  const updateVisibility = () => {
    const professional = versionWidget.value === "professional";
    if (!professional) {
      bitDepthWidget.value = "auto";
    }
    setWidgetVisible(node, sceneWidget, !professional);
    setWidgetVisible(node, bitDepthWidget, professional);
    app.graph.setDirtyCanvas(true, true);
  };

  const originalCallback = versionWidget.callback;
  versionWidget.callback = function (...args) {
    const result = originalCallback?.apply(this, args);
    updateVisibility();
    return result;
  };

  const originalOnConfigure = node.onConfigure;
  node.onConfigure = function (...args) {
    const result = originalOnConfigure?.apply(this, args);
    updateVisibility();
    return result;
  };

  updateVisibility();
}


app.registerExtension({
  name: "ComfyUI.SuperResolution.LocalVideoUpload",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (!["VolcengineVideoEnhance", "LASVideoSuperResolution"].includes(nodeData.name)) {
      return;
    }

    const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      const result = originalOnNodeCreated?.apply(this, args);
      addLocalVideoUploadButton(this);
      if (nodeData.name === "VolcengineVideoEnhance") {
        configureMediaKitVersionWidgets(this);
      }
      return result;
    };
  },
});
