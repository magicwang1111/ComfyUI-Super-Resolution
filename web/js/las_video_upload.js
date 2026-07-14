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
  if (!localVideoWidget || localVideoWidget.__lasUploadButtonAdded) {
    return;
  }
  localVideoWidget.__lasUploadButtonAdded = true;

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


app.registerExtension({
  name: "ComfyUI.SuperResolution.LocalVideoUpload",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "LASVideoSuperResolution") {
      return;
    }

    const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      const result = originalOnNodeCreated?.apply(this, args);
      addLocalVideoUploadButton(this);
      return result;
    };
  },
});
