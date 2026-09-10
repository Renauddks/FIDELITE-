// FIDÉLITÉ+ — interactions front-end (aucune dépendance externe)

document.addEventListener("DOMContentLoaded", function () {

    // Copier un lien individuel dans le presse-papier
    document.querySelectorAll(".bouton-lien").forEach(function (bouton) {
        bouton.addEventListener("click", function () {
            var lien = bouton.getAttribute("data-lien");
            if (!lien) return;

            var reussite = function () {
                var texteOriginal = bouton.textContent;
                bouton.textContent = "✅ Copié !";
                setTimeout(function () { bouton.textContent = texteOriginal; }, 1800);
            };

            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(lien).then(reussite).catch(function () {
                    window.prompt("Copiez ce lien :", lien);
                });
            } else {
                window.prompt("Copiez ce lien :", lien);
            }
        });
    });

    // Confirmation avant les actions irréversibles (réinitialisation, suppression)
    document.querySelectorAll("form[data-confirmation]").forEach(function (formulaire) {
        formulaire.addEventListener("submit", function (evenement) {
            var message = formulaire.getAttribute("data-confirmation");
            if (!window.confirm(message)) {
                evenement.preventDefault();
            }
        });
    });

    // Fermer une modale en cliquant en dehors de son contenu
    document.querySelectorAll(".modale").forEach(function (modale) {
        modale.addEventListener("click", function (evenement) {
            if (evenement.target === modale) {
                modale.classList.remove("modale-visible");
            }
        });
    });
});
