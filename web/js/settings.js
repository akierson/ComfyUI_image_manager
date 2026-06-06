import { app } from "../../../scripts/app.js";

const IM_PATH = "/image-manager";
const TOOLTIP = "Open Image Manager (Shift+Click opens in popup)";

const openImageManager = (event) => {
    const url = `${window.location.origin}${IM_PATH}`;
    if (event?.shiftKey) {
        window.open(url, "_blank", "width=1200,height=800,resizable=yes,scrollbars=yes");
    } else {
        window.open(url, "_blank");
    }
};

app.registerExtension({
    name: "ImageManager.Settings",

    actionBarButtons: [{
        icon: "icon-[lucide--images]",
        tooltip: TOOLTIP,
        onClick: openImageManager,
    }],

    async setup() {
        app.ui.settings.addSetting({
            id: "ImageManager.managed_folder",
            name: "Image Manager: Managed folder path",
            type: "text",
            defaultValue: "",
            tooltip: "Absolute path for managed image storage. Restart required to take effect.",
        });

        window.addEventListener("message", (event) => {
            if (event.data?.type === "loadWorkflow" && event.data.workflow) {
                app.loadGraphData(event.data.workflow);
            }
        });
    },
});
