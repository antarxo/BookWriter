# Καθαρισμός του τοπικού Git

1. Δημιούργησε tag στο τελευταίο probe πριν αντικαταστήσεις τον production φάκελο.
2. Κράτησε τα παλιά probes στο ιστορικό/tags ή σε ξεχωριστό archive branch.
3. Στο κύριο branch κράτησε μόνο τα περιεχόμενα αυτού του RC1.
4. Μην αντιγράψεις πάλι ZIP probes, render dumps, προσωρινά PDF ή φακέλους `_inspect` μέσα στο repository.
5. Πρότεινε `.gitignore`: `*.zip`, `_inspect/`, `render/`, `tmp/`, `*.bak`, `*~`, εκτός από ελεγχόμενα fixtures.
