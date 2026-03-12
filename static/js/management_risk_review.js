function downloadHTML() {
    const title = 'Management Review - Risk Assessment - window.MGMTRISKCFG.review_date';
    const htmlContent = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>${title}</title>
    <link href="/static/vendor/bootstrap/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="/static/vendor/bootstrap-icons/font/bootstrap-icons.css">
    
</head>
<body>
    <div class="container-fluid">
        ${document.getElementById('report-content').innerHTML}
    </div>
</body>
</html>`;
    
    const blob = new Blob([htmlContent], {type: 'text/html'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'Management_Review_Risk_Assessment_window.MGMTRISKCFG.review_date.html';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
