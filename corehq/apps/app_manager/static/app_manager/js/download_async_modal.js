$(function(){
    "use strict";

    COMMCAREHQ.AsyncDownloader = function($el, download_url){
        var self = this;
        self.POLL_FREQUENCY = 1500; //ms
        self.ERROR_MESSAGE = "Sorry, something went wrong with the download. " +
                             "If you see this repeatedly please report an issue.";
        self.download_url = download_url;

        self.$el = $el;
        self.el_id = $el.attr("id");
        self.$download_progress = self.$el.find("#" + self.el_id + "-download-progress");
        self.$downloading = self.$el.find("#" + self.el_id + "-downloading");

        self.init = function(){
            self.$download_progress.addClass("hide");
            self.$downloading.removeClass("hide");
        };

        self.startPollDownloadStatus = function(data){
            var keep_polling = true;
            var pollDownloadStatus = function(){
                if (keep_polling) {
                    $.ajax({
                        url: data.download_url,
                        success: function (resp) {
                            if (resp.trim().length) {
                                self.$downloading.addClass("hide");
                                self.$download_progress.html(resp).removeClass("hide");
                                if (self.$download_progress.find(".alert-success").length) {
                                    keep_polling = false;
                                }
                            }
                            if (keep_polling) {
                                setTimeout(pollDownloadStatus, self.POLL_FREQUENCY);
                            }
                        },
                        error: function (resp) {
                            self.downloadError(resp.responseText);
                        }
                    });
                }
            };


            self.$el.on("hidden hidden.bs.modal", function(){
                keep_polling = false;
                self.init();
            });

            pollDownloadStatus();
        };

        self.generateDownload = function(){
            $.ajax({
                url: self.download_url,
                type: "GET",
                dataType: "json",
                success: function(data){
                    self.startPollDownloadStatus(data);
                },
                error: function(){
                    self.downloadError(self.ERROR_MESSAGE);
                }
            });
        };

        self.downloadError = function(text){
            self.$downloading.addClass("hide");
            self.$download_progress.removeClass("hide").html(text);
        };

        self.$el.on("show show.bs.modal", self.generateDownload);
        self.init();
    };
}());
